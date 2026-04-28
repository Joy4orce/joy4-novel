import os, sys, glob, ctypes
out = r"F:\Joy4_Novel\_diag_claude.txt"
try:
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
except Exception:
    is_admin = "unknown"

lines = []
lines.append(f"sys.executable      = {sys.executable}")
lines.append(f"sys.version         = {sys.version}")
lines.append(f"os.getlogin         = {os.getlogin()}")
lines.append(f"USERNAME            = {os.environ.get('USERNAME')}")
lines.append(f"USERPROFILE         = {os.environ.get('USERPROFILE')}")
lines.append(f"APPDATA             = {os.environ.get('APPDATA')}")
lines.append(f"LOCALAPPDATA        = {os.environ.get('LOCALAPPDATA')}")
lines.append(f"is_admin            = {is_admin}")
lines.append(f"cwd                 = {os.getcwd()}")
lines.append("")

paths = [
    r"C:\Users\admin",
    r"C:\Users\admin\AppData",
    r"C:\Users\admin\AppData\Roaming",
    r"C:\Users\admin\AppData\Roaming\Claude",
    r"C:\Users\admin\AppData\Roaming\Claude\claude-code",
    r"C:\Users\admin\AppData\Roaming\Claude\claude-code\2.1.119",
    r"C:\Users\admin\AppData\Roaming\Claude\claude-code\2.1.119\claude.exe",
]
for p in paths:
    try:
        exists = os.path.exists(p)
        isdir = os.path.isdir(p)
        isfile = os.path.isfile(p)
        listing = ""
        if isdir:
            try:
                listing = " | listing=" + ", ".join(os.listdir(p)[:6])
            except Exception as e:
                listing = f" | listdir-err={e!r}"
        lines.append(f"{p}\n   exists={exists} isdir={isdir} isfile={isfile}{listing}")
    except Exception as e:
        lines.append(f"{p}  -> EXCEPTION {e!r}")

# Try alternatives
lines.append("")
lines.append("--- Alternative paths ---")
alt_appdata = os.environ.get("APPDATA", "")
if alt_appdata:
    p = os.path.join(alt_appdata, "Claude")
    lines.append(f"{p}  exists={os.path.exists(p)}")

# Glob from current APPDATA
pat = os.path.join(os.environ.get("APPDATA",""), "Claude", "claude-code", "*", "claude.exe")
lines.append(f"glob pattern: {pat}")
lines.append(f"glob result : {glob.glob(pat)}")

with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
