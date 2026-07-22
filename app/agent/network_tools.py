"""Tools de descoberta, cross-platform: dispositivos na rede local (ping sweep
+ tabela ARP/vizinhança) e periféricos USB conectados nesta máquina. Só
leitura — não mudam nada em lugar nenhum, por isso não passam pelo portão de
aprovação do `shell_tool.py` (mesma régua de `capturar_tela`: exige
`shell_level(user_id) is not None`, sem card).
"""
import concurrent.futures
import ipaddress
import json
import platform
import re
import shutil
import socket
import subprocess

from google.genai import types

from app.agent.shell_tool import shell_level

_SYSTEM = platform.system()  # Linux | Darwin | Windows
_MAX_HOSTS = 256  # teto pra não escanear uma sub-rede gigante


# --------------------------------------------------------------------------- #
# Dispositivos na rede
# --------------------------------------------------------------------------- #

def _local_subnet() -> ipaddress.IPv4Network | None:
    """Sub-rede da 1ª interface IPv4 não-loopback com endereço configurado.
    Sub-redes maiores que _MAX_HOSTS caem pro /24 que contém o IP local."""
    import psutil

    for name, addrs in psutil.net_if_addrs().items():
        if name.startswith(("lo", "docker", "veth", "br-", "virbr")):
            continue
        for a in addrs:
            if a.family != socket.AF_INET or a.address.startswith("127."):
                continue
            if not a.netmask:
                continue
            try:
                net = ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False)
            except ValueError:
                continue
            if net.num_addresses > _MAX_HOSTS:
                net = ipaddress.ip_network(f"{a.address}/24", strict=False)
            return net
    return None


def _ping(ip: str, timeout_s: float = 1.0) -> bool:
    if _SYSTEM == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), ip]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_s + 2)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _read_neighbors() -> dict[str, str]:
    """IP -> MAC, lendo a tabela de vizinhança/ARP do SO (o que já respondeu
    recentemente, mais o que o ping sweep acabou de popular)."""
    out: dict[str, str] = {}
    try:
        if shutil.which("ip"):
            r = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                m = re.match(r"^(\S+).*lladdr ([0-9a-fA-F:]{17})", line)
                if m:
                    out[m.group(1)] = m.group(2)
        elif shutil.which("arp"):
            r = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                m = re.search(
                    r"\(?(\d+\.\d+\.\d+\.\d+)\)?.*?([0-9a-fA-F]{1,2}(?:[:-][0-9a-fA-F]{1,2}){5})", line
                )
                if m:
                    out[m.group(1)] = m.group(2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def _reverse_dns(ip: str, timeout_s: float = 0.5) -> str | None:
    socket.setdefaulttimeout(timeout_s)
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, socket.timeout, OSError):
        return None
    finally:
        socket.setdefaulttimeout(None)


def scan_network() -> dict:
    """Ping sweep paralelo da sub-rede local + leitura da tabela de
    vizinhança. Nunca levanta — devolve {ok: False, error} se não der pra
    determinar a rede local."""
    net = _local_subnet()
    if net is None:
        return {"ok": False, "error": "não consegui determinar a rede local"}

    hosts = [str(h) for h in net.hosts()][:_MAX_HOSTS]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        alive = dict(zip(hosts, ex.map(_ping, hosts)))

    neighbors = _read_neighbors()
    devices = []
    for ip, up in alive.items():
        mac = neighbors.get(ip)
        if not up and not mac:
            continue  # nem respondeu ping nem está na tabela — ignora
        devices.append({
            "ip": ip,
            "mac": mac,
            "hostname": _reverse_dns(ip) if up else None,
            "reachable": up,
        })
    devices.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    return {"ok": True, "network": str(net), "devices": devices}


# --------------------------------------------------------------------------- #
# Dispositivos USB (nesta máquina)
# --------------------------------------------------------------------------- #

def _usb_linux() -> dict:
    if not shutil.which("lsusb"):
        return {"ok": False, "error": "lsusb não instalado (pacote usbutils)"}
    r = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=10)
    devices = []
    for line in r.stdout.splitlines():
        m = re.match(r"Bus \S+ Device \S+: ID (\S+) (.*)", line)
        if m:
            devices.append({"id": m.group(1), "description": m.group(2).strip()})
    return {"ok": True, "devices": devices}


def _usb_macos() -> dict:
    try:
        r = subprocess.run(
            ["system_profiler", "SPUSBDataType", "-json"], capture_output=True, text=True, timeout=15
        )
        data = json.loads(r.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"falha ao ler system_profiler: {exc}"}

    devices = []

    def _walk(items):
        for item in items or []:
            name = item.get("_name")
            if name:
                devices.append({"description": name, "vendor": item.get("manufacturer")})
            _walk(item.get("_items"))

    _walk(data.get("SPUSBDataType"))
    return {"ok": True, "devices": devices}


_USB_PS_SCRIPT = (
    "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB*' } "
    "| Select-Object FriendlyName,InstanceId | ConvertTo-Json -Compress"
)


def _usb_windows() -> dict:
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        return {"ok": False, "error": "powershell não encontrado"}
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-Command", _USB_PS_SCRIPT],
            capture_output=True, text=True, timeout=15,
        )
        raw = json.loads(r.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"falha ao ler saída do PowerShell: {exc}"}
    if isinstance(raw, dict):
        raw = [raw]
    devices = [{"description": d.get("FriendlyName"), "id": d.get("InstanceId")} for d in raw]
    return {"ok": True, "devices": devices}


def list_usb_devices() -> dict:
    if _SYSTEM == "Linux":
        return _usb_linux()
    if _SYSTEM == "Darwin":
        return _usb_macos()
    if _SYSTEM == "Windows":
        return _usb_windows()
    return {"ok": False, "error": f"SO não suportado: {_SYSTEM}"}


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

LISTAR_DISPOSITIVOS_REDE_DECL = types.FunctionDeclaration(
    name="listar_dispositivos_rede",
    description=(
        "Varre a rede local (mesma sub-rede desta máquina) e lista os "
        "dispositivos encontrados: IP, endereço MAC (se disponível) e nome de "
        "rede (se resolver por DNS reverso). Útil pra achar o endereço de algo "
        "antes de usar executar_ssh. Pode levar alguns segundos."
    ),
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)

LISTAR_DISPOSITIVOS_USB_DECL = types.FunctionDeclaration(
    name="listar_dispositivos_usb",
    description="Lista os dispositivos USB conectados NESTA máquina (pendrives, periféricos, etc.).",
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)


def _sem_permissao() -> dict:
    return {"ok": False, "error": "Sem permissão pra ver dispositivos — só o usuário principal pode."}


def listar_dispositivos_rede(user_id: int, args: dict) -> dict:
    if shell_level(user_id) is None:
        return _sem_permissao()
    return scan_network()


def listar_dispositivos_usb(user_id: int, args: dict) -> dict:
    if shell_level(user_id) is None:
        return _sem_permissao()
    return list_usb_devices()
