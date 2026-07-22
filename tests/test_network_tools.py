"""Tools de descoberta (rede/USB): gate de permissão + subprocess/psutil
sempre monkeypatchados — nunca varre a rede/hardware de verdade em teste."""
import app.agent.network_tools as network_tools


def test_normal_user_cannot_list_network(app, make_user):
    uid = make_user("normal")
    with app.app_context():
        r = network_tools.listar_dispositivos_rede(uid, {})
        assert r["ok"] is False


def test_normal_user_cannot_list_usb(app, make_user):
    uid = make_user("normal")
    with app.app_context():
        r = network_tools.listar_dispositivos_usb(uid, {})
        assert r["ok"] is False


def test_principal_can_list_network(app, make_user, monkeypatch):
    uid = make_user("p", is_principal=True)
    monkeypatch.setattr(network_tools, "scan_network", lambda: {"ok": True, "network": "10.0.0.0/24", "devices": []})
    with app.app_context():
        r = network_tools.listar_dispositivos_rede(uid, {})
        assert r["ok"] is True


def test_principal_can_list_usb(app, make_user, monkeypatch):
    uid = make_user("p", is_principal=True)
    monkeypatch.setattr(network_tools, "list_usb_devices", lambda: {"ok": True, "devices": []})
    with app.app_context():
        r = network_tools.listar_dispositivos_usb(uid, {})
        assert r["ok"] is True


def test_scan_network_sem_rede_local(monkeypatch):
    monkeypatch.setattr(network_tools, "_local_subnet", lambda: None)
    r = network_tools.scan_network()
    assert r["ok"] is False


def test_scan_network_pinga_e_le_vizinhanca(monkeypatch):
    import ipaddress

    monkeypatch.setattr(network_tools, "_local_subnet", lambda: ipaddress.ip_network("192.168.1.0/30"))
    # /30 tem 2 hosts utilizáveis: .1 e .2
    monkeypatch.setattr(network_tools, "_ping", lambda ip, timeout_s=1.0: ip.endswith(".1"))
    monkeypatch.setattr(network_tools, "_read_neighbors", lambda: {"192.168.1.2": "aa:bb:cc:dd:ee:ff"})
    monkeypatch.setattr(network_tools, "_reverse_dns", lambda ip, timeout_s=0.5: "meupc.local" if ip.endswith(".1") else None)

    r = network_tools.scan_network()
    assert r["ok"] is True
    ips = {d["ip"]: d for d in r["devices"]}
    assert ips["192.168.1.1"]["reachable"] is True
    assert ips["192.168.1.1"]["hostname"] == "meupc.local"
    assert ips["192.168.1.2"]["reachable"] is False
    assert ips["192.168.1.2"]["mac"] == "aa:bb:cc:dd:ee:ff"


def test_list_usb_devices_so_nao_suportado(monkeypatch):
    monkeypatch.setattr(network_tools, "_SYSTEM", "Plan9")
    r = network_tools.list_usb_devices()
    assert r["ok"] is False


def test_usb_linux_sem_lsusb(monkeypatch):
    monkeypatch.setattr(network_tools, "_SYSTEM", "Linux")
    monkeypatch.setattr(network_tools.shutil, "which", lambda name: None)
    r = network_tools.list_usb_devices()
    assert r["ok"] is False
    assert "lsusb" in r["error"]


def test_usb_linux_parseia_saida(monkeypatch):
    import subprocess

    monkeypatch.setattr(network_tools, "_SYSTEM", "Linux")
    monkeypatch.setattr(network_tools.shutil, "which", lambda name: "/usr/bin/lsusb")
    out = "Bus 001 Device 002: ID 8087:0aaa Intel Corp. Bluetooth\nBus 002 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""),
    )
    r = network_tools.list_usb_devices()
    assert r["ok"] is True
    assert len(r["devices"]) == 2
    assert r["devices"][0]["id"] == "8087:0aaa"
    assert "Bluetooth" in r["devices"][0]["description"]
