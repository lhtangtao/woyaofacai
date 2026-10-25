"""Reproducible local pyside6-deploy build; excludes all personal inputs."""
import configparser
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    deploy = Path(sys.executable).parent / "pyside6-deploy"
    if sys.platform != "darwin":
        raise SystemExit("macOS .app 必须在 macOS 构建。")
    subprocess.run([str(deploy), "desktop_entry.py", "--init", "--force", "--name", "Money3",
                    "--extra-ignore-dirs=.venv,references,reports,tests,docs,build,dist,deployment",
                    "--extra-modules=Core,Gui,Widgets"], cwd=root, check=True)
    config = configparser.ConfigParser()
    spec = root / "pysidedeploy.spec"
    config.read(spec)
    config["app"]["exec_directory"] = "dist"
    config["qt"]["plugins"] = "platforms,styles,imageformats,iconengines"
    config["nuitka"]["extra_args"] = ("--quiet --noinclude-qt-translations --include-package=portfolio --include-package=longbridge "
        "--include-data-dir=src/portfolio/resources=portfolio/resources "
        "--disable-cache=ccache "
        "--macos-app-name=Money3 --macos-app-version=0.3.0")
    with spec.open("w") as stream:
        config.write(stream)
    (root / "dist").mkdir(exist_ok=True)
    subprocess.run([str(deploy), "--config-file", str(spec), "--force", "--keep-deployment-files"], cwd=root, check=True)
    app = root / "dist/Money3.app"
    if not app.is_dir():
        raise SystemExit("打包器未生成预期 Money3.app，请检查 deployment 日志。")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    print("应用已生成：", app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
