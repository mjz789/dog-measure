# -*- mode: python ; coding: utf-8 -*-
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/dog_measure.ico', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtBluetooth',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickWidgets',
        'PySide6.QtSql',
        'PySide6.QtSvg',
        'PySide6.QtVirtualKeyboard',
        'PySide6.QtWebChannel',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
    ],
    noarchive=False,
    optimize=0,
)

# PySide6's generic hook collects plugins for optional modules that this
# QWidget application never imports. Keep the small Windows desktop runtime
# and discard QML, multimedia, PDF, network, and software-OpenGL payloads.
unused_qt_binaries = {
    'PySide6/Qt6Network.dll',
    'PySide6/Qt6OpenGL.dll',
    'PySide6/Qt6Pdf.dll',
    'PySide6/Qt6Qml.dll',
    'PySide6/Qt6QmlMeta.dll',
    'PySide6/Qt6QmlModels.dll',
    'PySide6/Qt6QmlWorkerScript.dll',
    'PySide6/Qt6Quick.dll',
    'PySide6/Qt6Svg.dll',
    'PySide6/Qt6VirtualKeyboard.dll',
    'PySide6/opengl32sw.dll',
}
allowed_qt_plugins = {
    'PySide6/plugins/imageformats/qico.dll',
    'PySide6/plugins/imageformats/qjpeg.dll',
    'PySide6/plugins/platforms/qwindows.dll',
    'PySide6/plugins/styles/qmodernwindowsstyle.dll',
}

def keep_binary(entry):
    destination = entry[0].replace('\\', '/')
    if destination.startswith('cv2/opencv_videoio_ffmpeg'):
        return False
    if destination in unused_qt_binaries:
        return False
    if destination.startswith('PySide6/plugins/'):
        return destination in allowed_qt_plugins
    return True

a.binaries = [entry for entry in a.binaries if keep_binary(entry)]
a.datas = [
    entry
    for entry in a.datas
    if not entry[0].replace('\\', '/').startswith('PySide6/translations/')
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='狗狗视觉测量',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='assets/dog_measure.ico',
    version='version_info.txt',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
