from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent


def _run(cmd: list[str] | str, *, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=str(ROOT), env=env, check=True)


def _run_capture(
    cmd: list[str] | str, *, env: dict[str, str] | None = None
) -> str:
    return subprocess.check_output(
        cmd, cwd=str(ROOT), env=env, text=True, stderr=subprocess.STDOUT
    )


def _clean_build_artifacts() -> None:
    for p in ("build", "dist", "__pycache__"):
        path = ROOT / p
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    path.unlink()
                except OSError:
                    pass


def _maybe_prompt_clean() -> bool:
    ans = input("Очистить build/ и dist/ перед сборкой? [y/N]: ").strip().lower()
    return ans in ("y", "yes", "д", "да")


def _prompt_pyi_extra_args() -> str:
    print(
        "Доп. аргументы для PyInstaller (например: --noconfirm --debug=all).",
    )
    v = input("PYI_EXTRA_ARGS (пусто = без доп. аргументов): ").strip()
    return v


def _has_wsl() -> bool:
    return shutil.which("wsl") is not None


def _has_gh() -> bool:
    return shutil.which("gh") is not None


def _get_gh_repo() -> str:
    repo = os.environ.get("GH_REPO", "").strip()
    if repo:
        return repo
    repo = input(
        "Введите GitHub репозиторий в формате owner/repo (или HOST/owner/repo): "
    ).strip()
    return repo


def _get_gh_ref() -> str:
    ref = os.environ.get("GH_REF", "").strip()
    if ref:
        return ref
    r = input("Branch/Tag для workflow (default: main): ").strip()
    return r or "main"


def _trigger_and_download_windows_via_ci(env: dict[str, str]) -> None:
    if not _has_gh():
        print(
            "Для сборки Windows на Linux нужен GitHub Actions (утилита `gh`). "
            "Установите `gh` или собирайте Windows на Windows/CI."
        )
        return

    repo = _get_gh_repo()
    ref = _get_gh_ref()

    out_dir = ROOT / "out" / "windows"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Проверим, что workflow вообще есть на GitHub.
    try:
        workflows_json = _run_capture(
            ["gh", "api", f"repos/{repo}/actions/workflows"],
            env=env,
        )
        if '"workflows":[]' in workflows_json.replace(" ", ""):
            print(
                "На GitHub в этом репозитории пока НЕТ workflow'ов, поэтому сборка Windows через CI не запустится.\n"
                "Нужно закоммитить и запушить файл `.github/workflows/build.yml` в ваш репозиторий,\n"
                "а затем повторить сборку."
            )
            return
    except subprocess.CalledProcessError as e:
        print("Не удалось проверить workflow'ы на GitHub.")
        if e.output:
            print(str(e.output))
        # не выходим: попробуем всё равно запустить

    print(f"CI: триггерю workflow `build.yml` для Windows в {repo}@{ref} ...")
    try:
        workflow_output = _run_capture(
            ["gh", "workflow", "run", "build.yml", "-r", ref, "-R", repo],
            env=env,
        )
    except subprocess.CalledProcessError as e:
        print("CI: `gh workflow run` вернул ошибку.")
        if e.output:
            print(e.output)
        else:
            print(str(e))
        print(
            "Проверьте, что workflow `.github/workflows/build.yml` существует в репозитории\n"
            "и закоммичен/запушен на указанную ветку/тег."
        )
        return

    # Ищем run-id в URL: .../actions/runs/<id>
    m = re.search(r"/actions/runs/(\d+)", workflow_output)
    if not m:
        print("Не удалось распарсить run-id из ответа `gh`.")
        print(workflow_output)
        return
    run_id = m.group(1)

    print(f"CI: жду завершения run {run_id} ...")
    _run(["gh", "run", "watch", run_id, "--exit-status", "-R", repo], env=env)

    print(f"CI: скачиваю артефакт `shefostycoon-windows` в {out_dir} ...")
    _run(
        [
            "gh",
            "run",
            "download",
            run_id,
            "-R",
            repo,
            "-n",
            "shefostycoon-windows",
            "-D",
            str(out_dir),
        ],
        env=env,
    )
    print("Готово: Windows build в out/windows")


def _trigger_and_download_linux_via_ci(env: dict[str, str]) -> None:
    if not _has_gh():
        print(
            "Для сборки Linux на Windows нужен GitHub Actions (утилита `gh`). "
            "Установите `gh` или собирайте Linux на Linux/CI."
        )
        return

    repo = _get_gh_repo()
    ref = _get_gh_ref()

    out_dir = ROOT / "out" / "linux"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        workflows_json = _run_capture(
            ["gh", "api", f"repos/{repo}/actions/workflows"],
            env=env,
        )
        if '"workflows":[]' in workflows_json.replace(" ", ""):
            print(
                "На GitHub в этом репозитории пока НЕТ workflow'ов, поэтому сборка Linux через CI не запустится.\n"
                "Нужно закоммитить и запушить файл `.github/workflows/build.yml` в ваш репозиторий,\n"
                "а затем повторить сборку."
            )
            return
    except subprocess.CalledProcessError as e:
        print("Не удалось проверить workflow'ы на GitHub.")
        if e.output:
            print(str(e.output))

    print(f"CI: триггерю workflow `build.yml` для Linux в {repo}@{ref} ...")
    try:
        workflow_output = _run_capture(
            ["gh", "workflow", "run", "build.yml", "-r", ref, "-R", repo],
            env=env,
        )
    except subprocess.CalledProcessError as e:
        print("CI: `gh workflow run` вернул ошибку.")
        if e.output:
            print(e.output)
        else:
            print(str(e))
        print(
            "Проверьте, что workflow `.github/workflows/build.yml` существует в репозитории\n"
            "и закоммичен/запушен на указанную ветку/тег."
        )
        return

    m = re.search(r"/actions/runs/(\d+)", workflow_output)
    if not m:
        print("Не удалось распарсить run-id из ответа `gh`.")
        print(workflow_output)
        return
    run_id = m.group(1)

    print(f"CI: жду завершения run {run_id} ...")
    _run(["gh", "run", "watch", run_id, "--exit-status", "-R", repo], env=env)

    print(f"CI: скачиваю артефакт `shefostycoon-linux` в {out_dir} ...")
    _run(
        [
            "gh",
            "run",
            "download",
            run_id,
            "-R",
            repo,
            "-n",
            "shefostycoon-linux",
            "-D",
            str(out_dir),
        ],
        env=env,
    )
    print("Готово: Linux build в out/linux")


def build_linux(env: dict[str, str]) -> None:
    if sys.platform == "win32":
        if _has_wsl():
            print("Windows: пробую собрать Linux в WSL...")
            _run(["wsl", "--", "bash", "-lc", "bash ./build_portable.sh"], env=env)
        else:
            print("WSL не найден — пробую GitHub Actions (CI) для Linux...")
            _trigger_and_download_linux_via_ci(env)
    else:
        print("Сборка для Linux...")
        _run(["bash", "./build_portable.sh"], env=env)


def build_windows(env: dict[str, str]) -> None:
    if sys.platform == "win32":
        print("Сборка для Windows...")
        _run(["cmd.exe", "/c", "build_portable.bat"], env=env)
    else:
        # Нативного кросс-компилирования Windows->Linux/Python->PyInstaller нет.
        # Поэтому используем GitHub Actions, если есть `gh` и указанный репозиторий.
        _trigger_and_download_windows_via_ci(env)


def main() -> None:
    print("Shefos Tycoon - Build menu")

    do_clean = _maybe_prompt_clean()
    pyi_extra = _prompt_pyi_extra_args()

    env = os.environ.copy()
    if pyi_extra:
        env["PYI_EXTRA_ARGS"] = pyi_extra

    if do_clean:
        print("Очищаю build/ и dist/...")
        _clean_build_artifacts()

    while True:
        print("")
        print("1) Скомпилировать для Linux")
        print("2) Скомпилировать для Windows")
        print("3) Скомпилировать: и Linux, и Windows (best effort)")
        print("4) Выйти")
        choice = input("Выберите пункт: ").strip()

        if choice == "1":
            build_linux(env)
        elif choice == "2":
            build_windows(env)
        elif choice == "3":
            build_linux(env)
            build_windows(env)
        elif choice == "4" or choice == "":
            print("Выход.")
            return
        else:
            print("Не понял выбор, попробуйте ещё раз.")


if __name__ == "__main__":
    main()

