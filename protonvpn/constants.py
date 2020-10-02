# https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
# Save settings in XDG_CONFIG_HOME
# Save cache XDG_CACHE_HOME
# Save logs and other user data in XDG_DATA_HOME

import os

from xdg import BaseDirectory
XDG_CACHE_HOME = BaseDirectory.xdg_cache_home
XDG_CONFIG_HOME = BaseDirectory.xdg_config_home
# XDG_DATA_HOME = BaseDirectory.xdg_data_home

from .enums import (
    ProtocolImplementationEnum,
    ProtocolEnum,
    FeatureEnum,
    UserSettingsEnum,
    UserSettingsStatusEnum
)

APP_VERSION = '0.0.4'

PWD = os.path.dirname(os.path.abspath(__file__))

TEMPLATES = os.path.join(PWD, "templates")
OPENVPN_TEMPLATE = "openvpn_template.j2"
PROTON_XDG_CACHE_HOME = os.path.join(XDG_CACHE_HOME, "protonvpn")
PROTON_XDG_CONFIG_HOME = os.path.join(XDG_CONFIG_HOME, "protonvpn")
PROTON_XDG_CACHE_HOME_LOGS = os.path.join(PROTON_XDG_CACHE_HOME, "logs")
CACHED_SERVERLIST = os.path.join(
    PROTON_XDG_CACHE_HOME, "cached_serverlist.json"
)
CACHED_OPENVPN_CERTIFICATE = os.path.join(
    PROTON_XDG_CACHE_HOME, "ProtonVPN.ovpn"
)
LOGFILE = os.path.join(PROTON_XDG_CACHE_HOME_LOGS, "protonvpn.log")
CONNECTION_STATE_FILEPATH = os.path.join(
    PROTON_XDG_CACHE_HOME, "connection_metadata.json"
)
USER_CONFIGURATIONS_FILEPATH = os.path.join(
    PROTON_XDG_CONFIG_HOME, "user_configurations.json"
)
DEFAULT_KEYRING_SERVICE = "ProtonVPN"
DEFAULT_KEYRING_USERNAME = "AuthData"
ENV_CI_NAME = "protonvpn_ci"

SUPPORTED_PROTOCOLS = {
    ProtocolImplementationEnum.OPENVPN: [ProtocolEnum.TCP, ProtocolEnum.UDP],
    # ProtocolImplementationEnum.STRONGSWAN: [ProtocolEnum.IKEV2],
    # ProtocolImplementationEnum.WIREGUARD: [ProtocolEnum.WIREGUARD],
}

FLAT_SUPPORTED_PROTOCOLS = [
    proto for proto_list
    in [v for k, v in SUPPORTED_PROTOCOLS.items()]
    for proto in proto_list
]

SUPPORTED_FEATURES = {
    FeatureEnum.NORMAL: "Normal",
    FeatureEnum.SECURE_CORE: "Secure-Core",
    FeatureEnum.TOR: "Tor",
    FeatureEnum.P2P: "P2P",
    FeatureEnum.STREAMING: "Streaming",
    FeatureEnum.IPv6: "IPv6"
}

CONFIG_STATUSES = [
    UserSettingsStatusEnum.DISABLED,
    UserSettingsStatusEnum.ENABLED,
    UserSettingsStatusEnum.CUSTOM,
]

VIRTUAL_DEVICE_NAME = "proton0"
LOGGER_NAME = "protonvpn"
APP_CONFIG = os.path.join(PWD, "app.cfg")

USER_CONFIG_TEMPLATE = {
    UserSettingsEnum.CONNECTION: {
        "default_protocol": ProtocolEnum.TCP,
        "killswitch": UserSettingsStatusEnum.DISABLED,
        "dns": {
            "status": UserSettingsStatusEnum.DISABLED,
            "custom_dns": []
        },
        "split_tunneling": {
            "status": UserSettingsStatusEnum.DISABLED,
            "ip_list": []
        }
    },
    UserSettingsEnum.GENERAL: {},
    UserSettingsEnum.ADVANCED: {},
    UserSettingsEnum.TRAY: {},
}

USAGE = """
ProtonVPN CLI
Usage:
    protonvpn-exp login
    protonvpn-exp logout
    protonvpn-exp (c | connect) [<servername>] [-p <protocol>]
    protonvpn-exp (c | connect) [-f | --fastest] [-p <protocol>]
    protonvpn-exp (c | connect) [--cc <code>] [-p <protocol>]
    protonvpn-exp (c | connect) [--sc] [-p <protocol>]
    protonvpn-exp (c | connect) [--p2p] [-p <protocol>]
    protonvpn-exp (c | connect) [--tor] [-p <protocol>]
    protonvpn-exp (c | connect) [-r | --random] [-p <protocol>]
    protonvpn-exp (d | disconnect)
    protonvpn-exp (s | status)
    protpnvpn-exp configure
    protonvpn-exp (-h | --help)
    protonvpn-exp (-v | --version)
Options:
    -f, --fastest       Select fastest ProtonVPN server.
    -r, --random        Select a random ProtonVPN server.
    --cc CODE           Determine country for fastest connect.
    --sc                Connect to fastest Secure-Core server.
    --p2p               Connect to fastest torrent server.
    --tor               Connect to fastest Tor server.
    -p PROTOCOL         Determine protocol (UDP or TCP).
    -h, --help          Show this help message.
    -v, --version       Display version.
Commands:
    login               Login ProtonVPN.
    logout              Logout ProtonVPN.
    configure           Configurations menu.
    c, connect          Connect to a ProtonVPN server.
    d, disconnect       Disconnect the current session.
    s, status           Show connection status.
Arguments:
    <servername>        Servername (CH#4, CA-CH#1, CH#18-TOR).
"""
