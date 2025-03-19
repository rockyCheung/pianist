#!/bin/bash
# build.sh - 跨平台打包脚本

# 强制使用虚拟环境
if [ -n "$VIRTUAL_ENV" ]; then
    echo "使用虚拟环境: $VIRTUAL_ENV"
else
    echo "请先激活虚拟环境！"
    exit 1
fi

# 检查PySide6是否安装
if ! python -c "import PySide6" &> /dev/null; then
    echo "错误：请先安装PySide6 (pip install pyside6)"
    exit 1
fi

# 检查Qt插件是否存在
QT_PLUGIN_PATH=$(python -c "from PySide6 import QtCore; print(QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.PluginsPath))")
if [ ! -d "$QT_PLUGIN_PATH" ]; then
    echo "错误：找不到Qt插件目录，请确认PySide6安装完整"
    exit 1
fi

# 定义工程名称和资源文件
APP_NAME="PianoApp"
RESOURCE_DIRS=("sounds" "help" "translations")
CONFIG_FILES=("config.json" "keymap.json")

# 清理旧构建
rm -rf build/ dist/ *.spec

# 创建平台特定的打包命令
case "$(uname -s)" in
    Linux*)
        PLATFORM="linux"
        PYINSTALLER_OPTS="--windowed --icon=MyIcon.icns"
        if ! command -v gst-install-1.0 &> /dev/null; then
            echo "请先安装GStreamer：sudo apt-get install gstreamer1.0-plugins-base"
            exit 1
        fi
        ;;
    Darwin*)
        PLATFORM="macos"
        PYINSTALLER_OPTS="--windowed --icon=MyIcon.icns"
        # 直接使用用户提供的QT路径
        QT_PATH=".venv/lib/python3.11/site-packages/PySide6/Qt"
        PYINSTALLER_OPTS+=" --add-binary=${QT_PATH}/lib/QtCore.framework/Versions/A/QtCore:PySide6/Qt/lib/"
        ;;
    CYGWIN*|MINGW32*|MSYS*)
        PLATFORM="windows"
        PYINSTALLER_OPTS="--windowed --icon=MyIcon.icns"
        RESOURCE_DIRS+=("win_dlls")
        ;;
    *)
        echo "不支持的操作系统"
        exit 1
        ;;
esac

# 添加PySide6的自动依赖收集
PYINSTALLER_OPTS+=" --collect-all PySide6"

# 生成数据文件参数
DATA_ARGS=""
for dir in "${RESOURCE_DIRS[@]}"; do
    DATA_ARGS+="--add-data $dir/*:$dir "
done
for file in "${CONFIG_FILES[@]}"; do
    DATA_ARGS+="--add-data $file:. "
done

# 创建钩子目录和文件 hiddenimports = collect_submodules('PySide6.scripts.deploy_lib')
mkdir -p hooks
cat > hooks/hook-PySide6.scripts.deploy_lib.py <<EOF
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
# 显式声明核心隐藏导入
_hidden_imports = [
    'PySide6.scripts.deploy_lib',  # PySide6内部工具模块
    'PySide6.scripts.project_lib',
    'scipy.special'        # Scipy缺失的C扩展模块
]
# 动态收集必要的子模块（按需添加）
# _hidden_imports += collect_submodules('PySide6.scripts.deploy_lib')  # 部署相关子模块
_hidden_imports += collect_submodules('PySide6.scripts.project_lib')
_hidden_imports += collect_submodules('scipy.special')               # 仅收集scipy.special子模块
# 排除非必要模块（可选）
_excluded_imports = [
    'scipy.tests',          # 测试模块
    'scipy._lib.tests'      # 测试相关代码
]
hiddenimports = [m for m in _hidden_imports if m not in _excluded_imports]
# 仅收集关键数据文件
datas = collect_data_files('PySide6.scripts.deploy_lib') + collect_data_files('PySide6.scripts.project_lib') + collect_data_files('scipy', subdir='special')

EOF

# 生成PyInstaller命令
pyinstaller \
    --noconfirm \
    --clean \
    --windowed \
    --log-level WARN \
    --hidden-import=rtmidi \
    --hidden-import=markdown.extensions \
    --hidden-import=PySide6.QtMultimedia \
    --hidden-import=PySide6.scripts.deploy_lib \
    --hidden-import=PySide6.scripts.project_lib \
    --hidden-import=scipy.special \
    --add-data "sounds/*:sounds" \
    --add-data "translations/*:translations" \
    --add-data "help/*:help" \
    --add-data "config.json:." \
    --add-data "$QT_PLUGIN_PATH/multimedia:qt-plugins/multimedia" \
    --additional-hooks-dir=./hooks \
    pianist.py

# 处理Qt多媒体插件
case $PLATFORM in
    linux)
        cp -r /usr/lib/x86_64-linux-gnu/qt5/plugins/media dist/$APP_NAME/
        ;;
    windows)
        cp -r "C:/Qt/6.5.0/mingw_64/plugins/multimedia" dist/$APP_NAME/
        ;;
    macos)
        cp -r "${QT_PATH}/plugins/multimedia" dist/$APP_NAME.app/Contents/MacOS/
        ;;
esac

# 创建版本信息文件（Windows）
if [ "$PLATFORM" == "windows" ]; then
    cat > versioninfo.txt <<EOF
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
    ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'FileDescription', u'Virtual Piano Application'),
        StringStruct(u'ProductName', u'PianoApp'),
        StringStruct(u'LegalCopyright', u'© 2025 Rocky Studio')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
EOF
    PYINSTALLER_OPTS+=" --version-file=versioninfo.txt"
fi

echo "打包完成！输出目录：dist/"