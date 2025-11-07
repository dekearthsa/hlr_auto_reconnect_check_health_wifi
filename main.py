#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess, shlex, time, sys

LOGTAG = "wifi-heartbeat"

# ---- ตั้งค่า SSID และ Password ----
SSID = "NSTDA-Wifi-Staff"
PASS = "abcDEF99"

# ---- เป้าหมายสำหรับ ping ตรวจเน็ต ----
PING_TARGETS = []
DNS_FALLBACKS = ["1.1.1.1", "8.8.8.8"]

def run(cmd, timeout=8, check=False):
    try:
        p = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=timeout, check=check)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout: {cmd}"
    except Exception as e:
        return 1, "", f"error: {e}"

def log(msg):
    print(f"{LOGTAG}: {msg}", flush=True)

def nm_wifi_if():
    rc, out, _ = run("nmcli -t -f DEVICE,TYPE dev status")
    if rc != 0:
        return None
    for line in out.splitlines():
        dev, typ = (line.split(":", 1) + [""])[:2]
        if typ == "wifi":
            return dev
    return None

def default_gw():
    rc, out, _ = run("ip route")
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("default via"):
            parts = line.split()
            if len(parts) >= 3:
                return parts[2]
    return None

def ping_once(iface, target, w=1):
    rc, *_ = run(f"ping -I {iface} -c1 -W{w} {target}", timeout=w+2)
    return rc == 0

def check_connectivity(iface):
    targets = list(PING_TARGETS)
    gw = default_gw()
    if gw:
        targets.insert(0, gw)
    targets.extend(DNS_FALLBACKS)
    for t in targets:
        if ping_once(iface, t, 1):
            return True
    return False

def nm_state_of(iface):
    rc, out, _ = run("nmcli -t -f DEVICE,STATE dev status")
    if rc != 0:
        return "unknown"
    for line in out.splitlines():
        dev, st = (line.split(":", 1) + [""])[:2]
        if dev == iface:
            return st
    return "unknown"

def nm_active_ssid(iface):
    rc, out, _ = run("nmcli -t -f NAME,DEVICE connection show --active")
    if rc != 0:
        return ""
    for line in out.splitlines():
        name, dev = (line.split(":", 1) + [""])[:2]
        if dev == iface:
            return name
    return ""

def nm_connect_with_ssid(ssid, password):
    # 1) ลองวิธีง่ายก่อน (NM มักเดา PSK ให้เอง)
    rc, out, err = run(f"nmcli -w 20 dev wifi connect {shlex.quote(ssid)} password {shlex.quote(password)}")
    if rc == 0:
        log(f"Connected to SSID={ssid} via dev wifi connect")
        return True

    # 2) ถ้าเจอ error key-mgmt หาย / โปรไฟล์ค้าง ให้สร้างโปรไฟล์แบบระบุ key-mgmt ชัดเจน
    log(f"'dev wifi connect' failed: {err or out}. Trying explicit profile with wpa-psk …")
    # ลบโปรไฟล์ชื่อเดียวกัน ถ้ามี
    run(f"nmcli con delete {shlex.quote(ssid)}")
    # สร้างโปรไฟล์ใหม่และใส่ key-mgmt + psk
    rc, out, err = run(f"nmcli con add type wifi ifname wlan0 con-name {shlex.quote(ssid)} ssid {shlex.quote(ssid)}")
    if rc != 0:
        log(f"Failed to add connection: {err or out}")
        return False
    rc, out, err = run(
        f"nmcli con modify {shlex.quote(ssid)} wifi-sec.key-mgmt wpa-psk wifi-sec.psk {shlex.quote(password)}"
    )
    if rc != 0:
        log(f"Failed to set wifi-sec on connection: {err or out}")
        return False
    rc, out, err = run(f"nmcli -w 20 con up id {shlex.quote(ssid)}")
    if rc == 0:
        log(f"Connected to SSID={ssid} via explicit wpa-psk profile")
        return True

    log(f"Failed to connect SSID={ssid}: {err or out}")
    return False


def try_reconnect(iface):
    run("nmcli radio wifi on")

    # พยายามเชื่อมต่อ SSID ที่ตั้งไว้
    if SSID and PASS:
        if nm_connect_with_ssid(SSID, PASS):
            log(f"reconnected via SSID='{SSID}'")
            return True

    # ลองสแกนและเชื่อมต่อโปรไฟล์อื่นที่รู้จัก
    run("nmcli dev wifi rescan")
    rc, out, _ = run("nmcli -t -f NAME,TYPE con show")
    if rc == 0:
        for line in out.splitlines():
            name, typ = (line.split(":", 1) + [""])[:2]
            if typ == "802-11-wireless":
                if run(f"nmcli -w 10 con up id {shlex.quote(name)}")[0] == 0:
                    log(f"reconnected via saved profile: {name}")
                    return True

    log("no wifi connection profiles found")
    return False

def main():
    iface = nm_wifi_if()
    if not iface:
        log("no wifi interface found (nmcli)")
        sys.exit(1)

    state = nm_state_of(iface)
    log(f"iface={iface} state={state}")

    if state != "connected":
        log("state not connected → trying reconnect …")
        if try_reconnect(iface):
            time.sleep(5)

    if check_connectivity(iface):
        ssid = nm_active_ssid(iface)
        log(f"online ✓ SSID='{ssid or 'unknown'}'")
        sys.exit(0)

    # ถ้ายังไม่ออนไลน์ → hard reset อุปกรณ์
    log("still offline → hard reset device …")
    run(f"nmcli dev disconnect {iface}")
    time.sleep(2)
    run(f"nmcli dev connect {iface}")
    time.sleep(3)

    ok = check_connectivity(iface)
    log("online ✓ after hard reset" if ok else "offline ✗ (will retry next timer tick)")
    sys.exit(0 if ok else 2)

if __name__ == "__main__":
    main()
