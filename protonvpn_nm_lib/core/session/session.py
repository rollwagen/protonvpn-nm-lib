import os
import random
import time

from ...constants import (APP_VERSION, CACHED_SERVERLIST, CLIENT_CONFIG,
                          STREAMING_ICONS_CACHE_TIME_PATH, STREAMING_SERVICES)
from ...enums import KeyringEnum, KillswitchStatusEnum
from ...exceptions import (API403Error, API10013Error, APIError,
                           APISessionIsNotValidError,
                           DefaultOVPNPortsNotFoundError, InsecureConnection,
                           JSONDataError, UnknownAPIError, APITimeoutError)
from ...logger import logger
from ..environment import ExecutionEnvironment


class ErrorStrategy:
    def __init__(self, func):
        self._func = func

    def __call__(self, session, *args, **kwargs):
        from proton.exceptions import (ConnectionTimeOutError,
                                       NewConnectionError, ProtonError,
                                       ProtonNetworkError, TLSPinningError,
                                       UnknownConnectionError)
        try:
            result = self._func(session, *args, **kwargs)
        except ProtonError as e:
            # Do we handle that error code?
            logger.exception(e)
            if hasattr(self, f'_handle_{e.code}'):
                return getattr(
                    self,
                    f'_handle_{e.code}')(e, session, *args, **kwargs)
            else:
                raise self._remap_protonerror(e)
        except ConnectionTimeOutError as e:
            logger.exception(e)
            raise APITimeoutError("Connection to API timed out")
        except TLSPinningError as e:
            logger.exception(e)
            raise InsecureConnection("TLS pinning failed, connection could be insecure")
        except (NewConnectionError, ProtonNetworkError) as e:
            logger.exception(e)
            raise APIError("An error occured while attempting to reach API")
        except UnknownConnectionError as e:
            logger.exception(e)
            raise UnknownAPIError("Unknown API error occured")

        return result

    def __get__(self, obj, objtype):
        """Support instance methods."""
        import functools
        return functools.partial(self.__call__, obj)

    def _call_without_error_handling(self, session, *args, **kwargs):
        """Call the function, without any advanced handlers, but still remap error codes"""
        from proton.api import ProtonError
        try:
            return self._func(session, *args, **kwargs)
        except ProtonError as e:
            raise self._remap_protonerror(e)

    def _remap_protonerror(self, e):
        raise

    def _call_with_error_remapping(self, session, *args, **kwargs):
        return self._func(session, *args, **kwargs)

    def _call_original_function(self, session, *args, **kwargs):
        return getattr(session, self.__func__.__name__)(*args, **kwargs)

    # Common handlers retries
    def handle_429(self, error, session, *args, **kwargs):
        logger.info("Catched 429 error, will retry")

        hold_request_time = error.headers["Retry-After"]
        try:
            hold_request_time = int(hold_request_time)
        except ValueError:
            # Wait at least two seconds, and up to 20
            hold_request_time = 2 + random.random() * 18

        logger.info("Retrying after {} seconds".format(hold_request_time))
        time.sleep(hold_request_time)

        # Retry
        return self._call_original_function(session, *args, **kwargs)

    def handle_503(self, error, session, *args, **kwargs):
        logger.info("Catched 503 error, retrying new request")

        # Wait between 2 and 10 seconds
        hold_request_time = 2 + random.random() * 8
        time.sleep(hold_request_time)
        return self._call_original_function(session, *args, **kwargs)


class ErrorStrategyLogout(ErrorStrategy):
    def _handle_401(self, error, session, *args, **kwargs):
        logger.info("Ignored a 401 at logout")
        return


class ErrorStrategyNormalCall(ErrorStrategy):
    def _handle_401(self, error, session, *args, **kwargs):
        logger.info("Catched 401 error, will refresh session and retry")
        session.refresh()
        # Retry (without error handling this time)
        return self._call_without_error_handling(session, *args, **kwargs)

    def _handle_403(self, error, session, *args, **kwargs):
        raise API403Error("Missing scopes. Required user re-authentication.")

    def _handle_10013(self, error, session, *args, **kwargs):
        raise API10013Error("Refresh token is invalid. Required user re-authentication.")


class ErrorStrategyAuthenticate(ErrorStrategy):
    pass


class ErrorStrategyRefresh(ErrorStrategy):
    pass


class APISession:
    """
    Class that represents a session in the API.

    We use three keyring entries:
    1) DEFAULT_KEYRING_PROTON_USER (username)
    2) DEFAULT_KEYRING_SESSIONDATA (session data)
    3) DEFAULT_KEYRING_USERDATA (vpn data)

    These are checked using the following logic:
    - 1) or 2) missing => destroy all entries 1) and 2) and restart.
    - There's no valid reason why 1) would be missing but not 2),
        so we don't bother with logout in that case
    - 3) missing, but 1) and 2) are valid => fetch it from API.
    - 3) present, but 1) and 2) are missing => use it, but beware that
        API calls will fail (we could connect to VPN using cached data though)

    """

    # Probably would be better to have that somewhere else
    FULL_CACHE_TIME_EXPIRE = 180 * 60  # 180min in seconds
    STREAMING_SERVICES_TIME_EXPIRE = FULL_CACHE_TIME_EXPIRE
    CLIENT_CONFIG_TIME_EXPIRE = FULL_CACHE_TIME_EXPIRE
    STREAMING_ICON_TIME_EXPIRE = 480 * 60  # 480min in seconds
    LOADS_CACHE_TIME_EXPIRE = 15 * 60  # 15min in seconds
    RANDOM_FRACTION = 0.22  # Generate a value of the timeout, +/- up to 22%, at random

    def __init__(self, api_url=None, enforce_pinning=True):
        if api_url is None:
            self._api_url = "https://api.protonvpn.ch"

        self._enforce_pinning = enforce_pinning

        self.__session_create()

        self.__proton_user = None
        self.__vpn_data = None
        self.__vpn_logicals = None
        self.__clientconfig = None
        self.__streaming_services = None
        self.__streaming_icons = None

        # Load session
        try:
            self.__keyring_load_session()
        # FIXME: be more precise here to show correct message to the user
        except Exception as e:
            # What is thrown here are errors for accessing/parsing the keyring.
            # print("Couldn't load session, you'll have to login again")
            logger.exception(e)

    def __session_create(self):
        from proton.api import Session
        self.__proton_api = Session(
            self._api_url,
            appversion="LinuxVPN_" + APP_VERSION,
            user_agent=ExecutionEnvironment().user_agent,
            TLSPinning=self._enforce_pinning,
        )

    def __keyring_load_session(self):
        """
        Try to load username and session data from keyring:
        - If any of these are missing, delete the remainder from the keyring
        - if api_url doesn't match, just don't load the session
            (as it's for a different API)
        """
        try:
            keyring_data_user = ExecutionEnvironment().keyring[
                KeyringEnum.DEFAULT_KEYRING_PROTON_USER.value
            ]
        except KeyError:
            # We don't have user data, just give up
            self.__keyring_clear_session()
            return

        try:
            keyring_data = ExecutionEnvironment().keyring[
                KeyringEnum.DEFAULT_KEYRING_SESSIONDATA.value
            ]
        except KeyError:
            # No entry from keyring, just abort here
            self.__keyring_clear_session()
            return

        if keyring_data.get('api_url') != self.__proton_api.dump()['api_url']:
            # Don't reuse a session with different api url
            # FIXME
            # print("Wrong session url")
            return

        # We need a username there
        if 'proton_username' not in keyring_data_user:
            raise JSONDataError("Invalid format in KEYRING_PROTON_USER")

        # Only now that we know everything is working, we will set info
        # in the class
        from proton.api import Session

        # This is a "dangerous" call, as we assume that everything
        # in keyring_data is correctly formatted
        self.__proton_api = Session.load(
            keyring_data, TLSPinning=self._enforce_pinning
        )
        self.__proton_user = keyring_data_user['proton_username']

    def __keyring_clear_session(self):
        for k in [
            KeyringEnum.DEFAULT_KEYRING_SESSIONDATA,
            KeyringEnum.DEFAULT_KEYRING_PROTON_USER
        ]:
            try:
                del ExecutionEnvironment().keyring[k.value]
            except KeyError:
                pass

    def __keyring_clear_vpn_data(self):
        try:
            del ExecutionEnvironment().keyring[
                KeyringEnum.DEFAULT_KEYRING_USERDATA.value
            ]
        except KeyError:
            pass

    @ErrorStrategyLogout
    def logout(self):
        self.__keyring_clear_vpn_data()
        self.__keyring_clear_session()

        self.__proton_user = None
        self.__vpn_data = None

        self.__vpn_logicals = None

        # A best effort is to logout the user via
        # the API, but if that is not possible then
        # at the least logout the user locally.
        try:
            self.__proton_api.logout()
        except: # noqa
            pass
        self.remove_cache(CACHED_SERVERLIST)
        self.remove_cache(CLIENT_CONFIG)
        # Re-create a new
        self.__session_create()

    @ErrorStrategyRefresh
    def refresh(self):
        self.ensure_valid()

        self.__proton_api.refresh()
        # We need to store again the session data
        ExecutionEnvironment().keyring[
            KeyringEnum.DEFAULT_KEYRING_SESSIONDATA.value
        ] = self.__proton_api.dump()

    @ErrorStrategyAuthenticate
    def authenticate(self, username, password):
        """Authenticate using username/password.

        This destroys the current session, if any.
        """

        # Clear keyring and private data, and ensure we start fresh
        self.logout()

        # (try) to log in
        self.__proton_api.authenticate(username, password)

        # Order is important here: we first want to set keyrings,
        # then set the class status to avoid inconstistencies
        ExecutionEnvironment().keyring[
            KeyringEnum.DEFAULT_KEYRING_SESSIONDATA.value
        ] = self.__proton_api.dump()
        ExecutionEnvironment().keyring[
            KeyringEnum.DEFAULT_KEYRING_PROTON_USER.value
        ] = {"proton_username": username}

        self.__proton_user = username

        # immediatly cache all necessary data
        data_to_cache_list = [
            self.servers, self.clientconfig,
            self.streaming, self.streaming_icons,
            self._vpn_data
        ]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor() as executor:
            executor.map(self.__cache_data_after_authenticate, data_to_cache_list)

    def __cache_data_after_authenticate(self, data):
        try:
            data
        except Exception as e:
            logger.exception(e)
            return

    @property
    def is_valid(self):
        """
        Return True if the we believe a valid proton session.

        It doesn't check however if the session is working on the API though,
        so an API call might still fail.
        """
        # We use __proton_user as a proxy, since it's defined if and only if
        # we have a logged-in session in __proton_api
        return self.__proton_user is not None

    def ensure_valid(self):
        if not self.is_valid:
            raise APISessionIsNotValidError("No session")

    def remove_cache(self, cache_path):
        try:
            os.remove(cache_path)
        except FileNotFoundError:
            pass

    @property
    def username(self):
        # Return the proton username
        self.ensure_valid()
        return self.__proton_user

    @ErrorStrategyNormalCall
    def __vpn_data_fetch_from_api(self):
        self.ensure_valid()

        api_vpn_data = self.__proton_api.api_request('/vpn')
        self.__vpn_data = {
            'username': api_vpn_data['VPN']['Name'],
            'password': api_vpn_data['VPN']['Password'],
            'tier': api_vpn_data['VPN']['MaxTier']
        }

        # We now have valid VPN data, store it in the keyring
        ExecutionEnvironment().keyring[
            KeyringEnum.DEFAULT_KEYRING_USERDATA.value
        ] = self.__vpn_data

    @property
    def _vpn_data(self):
        """Get the vpn information.

        This is protected: we don't want anybody trying to mess
        with the JSON directly
        """
        # We have a local cache
        if self.__vpn_data is None:
            try:
                self.__vpn_data = ExecutionEnvironment().keyring[
                    KeyringEnum.DEFAULT_KEYRING_USERDATA.value
                ]
            except KeyError:
                # We couldn't load it from the keyring,
                # but that's really not something exceptional.
                self.__vpn_data_fetch_from_api()

        return self.__vpn_data

    @property
    def vpn_username(self):
        return self._vpn_data['username']

    @property
    def vpn_password(self):
        return self._vpn_data['password']

    @property
    def vpn_tier(self):
        return self._vpn_data['tier']

    def __generate_random_component(self):
        # 1 +/- 0.22*random
        return (1 + self.RANDOM_FRACTION * (2 * random.random() - 1))

    def _update_next_fetch_logicals(self):
        self.__next_fetch_logicals = self \
            .__vpn_logicals.logicals_update_timestamp + \
            self.FULL_CACHE_TIME_EXPIRE * self.__generate_random_component()

    def _update_next_fetch_loads(self):
        self.__next_fetch_load = self \
            .__vpn_logicals.loads_update_timestamp + \
            self.LOADS_CACHE_TIME_EXPIRE * self.__generate_random_component()

    def _update_next_fetch_client_config(self):
        self.__next_fetch_client_config = self \
            .__clientconfig.client_config_timestamp + \
            self.CLIENT_CONFIG_TIME_EXPIRE * self.__generate_random_component()

    def _update_next_fetch_streaming_services(self):
        self.__next_fetch_streaming_service = self \
            .__streaming_services.streaming_services_timestamp + \
            self.STREAMING_SERVICES_TIME_EXPIRE * self.__generate_random_component()

    def _update_next_fetch_streaming_icons(self):
        self.__next_fetch_streaming_icons = self \
            .__streaming_icons.streaming_icons_timestamp + \
            self.STREAMING_ICON_TIME_EXPIRE * self.__generate_random_component()

    @ErrorStrategyNormalCall
    def update_servers_if_needed(self, force=False):
        changed = False

        if (
            ExecutionEnvironment().settings.killswitch
            == KillswitchStatusEnum.HARD
            and not force
        ):
            return

        if self.__next_fetch_logicals < time.time() or force:
            # Update logicals
            self.__vpn_logicals.update_logical_data(
                self.__proton_api.api_request('/vpn/logicals')
            )
            changed = True
        elif self.__next_fetch_load < time.time():
            # Update loads
            self.__vpn_logicals.update_load_data(
                self.__proton_api.api_request(
                    '/vpn/loads'
                )
            )
            changed = True

        if changed:
            self._update_next_fetch_logicals()
            self._update_next_fetch_loads()

            try:
                with open(CACHED_SERVERLIST, "w") as f:
                    f.write(self.__vpn_logicals.json_dumps())
            except Exception as e:
                # This is not fatal, we only were not capable
                # of storing the cache.
                logger.info("Could not save server cache {}".format(
                    e
                ))

    @property
    def servers(self):
        if self.__vpn_logicals is None:
            from ..servers import ServerList

            # Create a new server list
            self.__vpn_logicals = ServerList()

            # Try to load from file
            try:
                with open(CACHED_SERVERLIST, "r") as f:
                    self.__vpn_logicals.json_loads(f.read())
            except FileNotFoundError:
                # This is not fatal,
                # we only were not capable of loading the cache.
                logger.info("Could not load server cache")

            self._update_next_fetch_logicals()
            self._update_next_fetch_loads()

        try:
            self.update_servers_if_needed()
        except: # noqa
            pass

        self.streaming
        return self.__vpn_logicals

    @ErrorStrategyNormalCall
    def update_client_config_if_needed(self, force=False):
        changed = False

        if (
            ExecutionEnvironment().settings.killswitch
            == KillswitchStatusEnum.HARD
            and not force
        ):
            return

        if self.__next_fetch_client_config < time.time() or force:
            # Update client config
            self.__clientconfig.update_client_config_data(
                self.__proton_api.api_request(
                    "/vpn/clientconfig"
                )
            )
            changed = True

        if changed:
            self._update_next_fetch_client_config()
            try:
                with open(CLIENT_CONFIG, "w") as f:
                    f.write(self.__clientconfig.json_dumps())
            except Exception as e:
                # This is not fatal, we only were not capable
                # of storing the cache.
                logger.info("Could not save client config cache {}".format(
                    e
                ))

    @property
    def clientconfig(self):
        if self.__clientconfig is None:
            from ..client_config import ClientConfig

            # Create a new client config
            self.__clientconfig = ClientConfig()

            # Try to load from file
            try:
                with open(CLIENT_CONFIG, "r") as f:
                    self.__clientconfig.json_loads(f.read())
            except FileNotFoundError:
                # This is not fatal,
                # we only were not capable of loading the cache.
                logger.info("Could not load client config cache")

            self._update_next_fetch_client_config()

        try:
            self.update_client_config_if_needed()
        except: # noqa
            pass

        return self.__clientconfig

    @ErrorStrategyNormalCall
    def update_streaming_data_if_needed(self, force=False):
        changed = False

        if (
            ExecutionEnvironment().settings.killswitch
            == KillswitchStatusEnum.HARD
            and not force
        ):
            return

        if self.__next_fetch_streaming_service < time.time() or force:
            # Update streaming services
            self.__streaming_services.update_streaming_services_data(
                self.__proton_api.api_request(
                    "/vpn/streamingservices"
                )
            )
            changed = True

        if changed:
            self._update_next_fetch_streaming_services()
            try:
                with open(STREAMING_SERVICES, "w") as f:
                    f.write(self.__streaming_services.json_dumps())
            except Exception as e:
                # This is not fatal, we only were not capable
                # of storing the cache.
                logger.info("Could not save streaming services cache {}".format(
                    e
                ))

    @property
    def streaming(self):
        if self.__streaming_services is None:
            from ..streaming import Streaming

            # create new Streaming object
            self.__streaming_services = Streaming()

            # Try to load from file
            try:
                with open(STREAMING_SERVICES, "r") as f:
                    self.__streaming_services.json_loads(f.read())
            except FileNotFoundError:
                # This is not fatal,
                # we only were not capable of loading the cache.
                logger.info("Could not load streaming cache")

            self._update_next_fetch_streaming_services()

        try:
            self.update_streaming_data_if_needed()
        except: # noqa
            pass

        self.streaming_icons

        return self.__streaming_services

    def update_streaming_icons_if_needed(self, force=False):
        if (
            ExecutionEnvironment().settings.killswitch
            == KillswitchStatusEnum.HARD
            and not force
        ):
            return

        if self.__next_fetch_streaming_icons < time.time() or force:
            self.__streaming_icons.update_streaming_icons_data(self.__streaming_services)

            self._update_next_fetch_streaming_icons()
            try:
                with open(STREAMING_ICONS_CACHE_TIME_PATH, "w") as f:
                    f.write(self.__streaming_icons.json_dumps())
            except Exception as e:
                # This is not fatal, we only were not capable
                # of storing the cache.
                logger.info("Could not save streaming services cache {}".format(
                    e
                ))

    @property
    def streaming_icons(self):
        if self.__streaming_icons is None:
            from ..streaming import StreamingIcons

            # create new StreamingIcon object
            self.__streaming_icons = StreamingIcons()
            try:
                with open(STREAMING_ICONS_CACHE_TIME_PATH, "r") as f:
                    self.__streaming_icons.json_loads(f.read())
            except FileNotFoundError:
                # This is not fatal,
                # we only were not capable of loading the cache.
                logger.info("Could not load streaming time cache")

            self._update_next_fetch_streaming_icons()

        try:
            self.update_streaming_icons_if_needed()
        except: # noqa
            pass

        return self.__streaming_icons

    @property
    def vpn_ports_openvpn_udp(self):
        try:
            return self.clientconfig.default_udp_ports
        except (TypeError, KeyError) as e:
            logger.exception(e)
            raise DefaultOVPNPortsNotFoundError(
                "Default OVPN ports could not be found"
            )

    @property
    def vpn_ports_openvpn_tcp(self):
        try:
            return self.clientconfig.default_tcp_ports
        except (TypeError, KeyError) as e:
            logger.exception(e)
            raise DefaultOVPNPortsNotFoundError(
                "Default OVPN ports could not be found"
            )
