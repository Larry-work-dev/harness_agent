"""執行使用者 code 技能——每次呼叫是這個容器裡的一個新 subprocess。

老實說這是什麼等級的隔離：這個容器本身在 docker-compose.yml 被限制成
mem_limit/cpus/pids_limit、獨立的 skill-net（internal:true，沒有出網、也連不到
db_api/backend）、no-new-privileges、cap_drop:ALL、唯讀根檔案系統；沒有掛
docker socket，沒辦法真的每次執行都開一支全新容器做隔離，所以是「同一個
容器裡跑 subprocess，靠 wall-clock timeout + RLIMIT_CPU/NPROC + 容器層 cgroup
記憶體上限」這種比較輕量的隔離——不是 gVisor/Firecracker 等級、不同技能的
執行會共用同一份 cgroup 資源預算（吵鬧的鄰居問題沒有完全解決），
kernel/container-escape 風險原則上還是存在。這些殘留風險已經在 plan 文件
講清楚，這裡不重複假裝隔離程度比實際上更高。
"""
import json
import os
import resource
import subprocess
import tempfile
import threading

_SENTINEL = "___SKILL_RESULT___"
_MAX_OUTPUT = 32 * 1024
_sem = threading.Semaphore(int(os.environ.get("SKILL_RUNNER_MAX_CONCURRENT", "4")))

# 把使用者原始碼包成完整可執行腳本：使用者只要定義 run(...)，driver 負責讀
# 參數、呼叫、把回傳值印在一個不會跟一般 print()/console.log() 撞在一起的
# sentinel 後面，才能可靠地把「使用者自己印的東西」跟「真正的回傳值」分開。
_DRIVERS = {
    "python": ("python3", "skill.py", (
        "\n\nif __name__ == '__main__':\n"
        "    import json as _json, sys as _sys\n"
        "    _args = _json.loads(_sys.argv[1])\n"
        "    _result = run(**_args)\n"
        "    print('{sentinel}' + _json.dumps(_result))\n"
    )),
    "javascript": ("node", "skill.js", (
        "\n\n(function() {{\n"
        "  const _args = JSON.parse(process.argv[2]);\n"
        "  const _result = run(_args);\n"
        "  console.log('{sentinel}' + JSON.stringify(_result));\n"
        "}})();\n"
    )),
}


def _limit_resources(cpu_s: int):
    """preexec_fn：只設 CPU 時間跟行程數上限。故意不設 RLIMIT_AS（虛擬記憶體
    位址空間）——Node 的 V8 開機就會保留遠大於實際會用到的虛擬位址空間，設
    RLIMIT_AS 很容易連「什麼都沒做」的空 script 都直接啟動失敗。真正的記憶體
    上限交給容器層的 cgroup mem_limit（照實際 RSS 算，不是虛擬位址空間）。"""
    def _apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    return _apply


def run_code(language: str, source: str, args: dict, timeout_s: int) -> dict:
    interpreter, filename, driver_tpl = _DRIVERS[language]
    driver = driver_tpl.format(sentinel=_SENTINEL)

    if not _sem.acquire(timeout=timeout_s + 2):
        return {"error": "執行佇列已滿，請稍後再試"}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            script_path = os.path.join(tmp, filename)
            with open(script_path, "w") as f:
                f.write(source + driver)
            try:
                proc = subprocess.run(
                    [interpreter, script_path, json.dumps(args)],
                    cwd=tmp, timeout=timeout_s, capture_output=True, text=True,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    preexec_fn=_limit_resources(timeout_s),
                )
            except subprocess.TimeoutExpired:
                return {"error": f"執行逾時（{timeout_s} 秒）"}

            stdout = (proc.stdout or "")[:_MAX_OUTPUT]
            if proc.returncode != 0:
                return {"stdout": stdout, "error": (proc.stderr or "執行失敗，沒有錯誤輸出")[:_MAX_OUTPUT]}

            if _SENTINEL in stdout:
                before, _, after = stdout.rpartition(_SENTINEL)
                try:
                    return {"stdout": before, "result": json.loads(after)}
                except Exception:
                    return {"stdout": before, "result": after.strip()}
            return {"stdout": stdout}
    finally:
        _sem.release()
