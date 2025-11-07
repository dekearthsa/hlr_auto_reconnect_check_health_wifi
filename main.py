
import  subprocess, shlex, time, sys

LOGTAG = "wifi-heartbeat"
PREFERRED = ("NSTD-Wifi-Staff", "abcDEF99")  # ตั้งชื่อ connection หลักได้ผ่าน env
PING_TARGETS = []  # จะเติม gateway ทีหลัง + DNS สาธารณะ
DNS_FALLBACKS = ["1.1.1.1", "8.8.8.8"]

def run(cmd, timeout=8, check=False):
    # บันทึก stdout/stderr ลง journalctl อัตโนมัติ (systemd เก็บให้)
    try:
        p = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=timeout, check=check)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout: {cmd}"
    except Exception as e:
        return 1, "", f"error: {e}"

def log(msg):
    # เขียนออก stdout เพื่อให้ systemd journal เก็บ
    print(f"{LOGTAG}: {msg}", flush=True)

def nm_wifi_if():
    rc, out, _ = run("nmcli -t -f DEVICE,TYPE dev status")
    if rc != 0: return None
    for line in out.splitlines():
        dev, typ = (line.split(":",1)+[""])[:2]
        if typ == "wifi":
            return dev
    return None

def default_gw():
    rc, out, _ = run("ip route")
    if rc != 0: return None
    for line in out.splitlines():
        if line.startswith("default via"):
            parts = line.split()
            # default via <gw> dev <iface> ...
            if len(parts) >= 3:
                return parts[2]
    return None

def ping_once(iface, target, w=1):
    rc, *_ = run(f"ping -I {iface} -c1 -W{w} {target}", timeout=w+2)
    return rc == 0

def check_connectivity(iface):
    # target ตัวแรกลอง gateway ก่อน ถ้าไม่เจอค่อยลอง DNS
    targets = list(PING_TARGETS) or []
    gw = default_gw()
    if gw: targets.insert(0, gw)
    targets.extend(DNS_FALLBACKS)
    for t in targets:
        if ping_once(iface, t, 1):
            return True
    return False

def nm_state_of(iface):
    rc, out, _ = run("nmcli -t -f DEVICE,STATE dev status")
    if rc != 0: return "unknown"
    for line in out.splitlines():
        dev, st = (line.split(":",1)+[""])[:2]
        if dev == iface:
            return st
    return "unknown"

def nm_active_ssid(iface):
    rc, out, _ = run("nmcli -t -f NAME,DEVICE connection show --active")
    if rc != 0: return ""
    for line in out.splitlines():
        name, dev = (line.split(":",1)+[""])[:2]
        if dev == iface:
            return name
    return ""

def nm_up_connection(name):
    return run(f"nmcli -w 15 con up id {shlex.quote(name)}")[0] == 0

def try_reconnect(iface):
    # เปิดวิทยุไว้ก่อน
    run("nmcli radio wifi on")

    # ถ้ากำหนด PREFERRED_SSID มาก็ลองก่อน
    tried_any = False
    if PREFERRED:
        tried_any = True
        if nm_up_connection(PREFERRED):
            log(f"reconnected via preferred: {PREFERRED}")
            return True

    # ไม่งั้นลอง active ssid ปัจจุบัน (ถ้ามีชื่อ)
    cur = nm_active_ssid(iface)
    if cur and not PREFERRED:
        tried_any = True
        if nm_up_connection(cur):
            log(f"reconnected via current: {cur}")
            return True

    # สแกน แล้วไล่ลองทุก connection ที่เป็น wifi
    run("nmcli dev wifi rescan")
    rc, out, _ = run("nmcli -t -f NAME,TYPE con show")
    if rc == 0:
        for line in out.splitlines():
            name, typ = (line.split(":",1)+[""])[:2]
            if typ == "802-11-wireless":
                tried_any = True
                if nm_up_connection(name):
                    log(f"reconnected via: {name}")
                    return True

    if not tried_any:
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
            time.sleep(2)

    if check_connectivity(iface):
        ssid = nm_active_ssid(iface)
        log(f"online ✓ SSID='{ssid or 'unknown'}'")
        sys.exit(0)

    # ถ้ายังไม่ออนไลน์ ลองอีกรอบแบบหนักขึ้น: disconnect/connect อุปกรณ์
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
