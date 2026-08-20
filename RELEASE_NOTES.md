# 狗狗视觉测量 v1.1.3 / Dog Vision Measure v1.1.3

本次版本优化测量控制点的精确选择体验，并继续提供约 46 MB 的 Windows 免安装 EXE，保留自动测量、UVC 摄像头和全部二维工具。/ This release improves precise measurement-handle selection and continues to provide an approximately 46 MB portable Windows EXE with automatic measurement, UVC camera capture, and the complete 2D toolset.

**操作视频 / Operation Video:** 请在项目 Releases 页面查看。/ See the project Releases page.

## 主要功能 / Highlights

- 自动识别圆并给出直径与圆心，无需人工寻找圆心。
- 选择两个已测圆，一键测量圆心之间的距离。
- 圆心距和三圆阵列均支持直接在画面点击圆，也支持从列表选择。
- 画面多选与测量列表双向同步高亮。
- 自动排除已测圆，支持相邻同心圆与同心度测量。
- 从三个已检测圆自动绘制圆周阵列外接圆并计算直径。
- 自动识别常见公制标尺，支持两点手动标定。
- 自动吸附边缘和角点，提供完整二维测量工具。
- UVC 摄像头实时测量与同名 PNG + CSV 一键导出。
- 标定及所有可编辑测量控制点统一使用细红色十字，减少对被测画面的遮挡。
- 拖动测量点或标定点时自动隐藏鼠标箭头，松开后恢复，便于精确对准边缘。
- 精确移除未使用的 Qt 可选模块和 OpenCV 视频文件解码组件，并使用 UPX 压缩原生库。
- 通过 60 项自动化工作流测试。

- Automatically detects circles and reports diameter and center coordinates without manual center picking.
- Measures the distance between two detected circle centers with one action.
- Center-distance and three-circle-array workflows accept direct canvas selection or result-list selection.
- Canvas multi-selection and result-list highlighting stay synchronized.
- Excludes previously measured circles for nearby concentric-ring and concentricity measurement.
- Builds a circular array from three detected circle centers and reports its diameter.
- Detects common metric rulers automatically, with two-point manual calibration as fallback.
- Snaps to nearby edges and corners and provides a complete planar measurement toolset.
- Supports live UVC measurement and one-click same-named PNG + CSV export.
- Uses thin red crosshairs for calibration and all editable measurement handles to minimize image obstruction.
- Hides the mouse pointer while dragging a measurement or calibration handle, then restores it on release for precise edge alignment.
- Removes unused optional Qt modules and OpenCV video-file decoding payloads, then compresses native libraries with UPX.
- Passes all 60 automated workflow tests.

## Windows 免安装版 / Portable Build

在项目页面右侧 **Releases** 下下载 `DogVisionMeasure-v1.1.3-Windows-x64.exe`，Windows 系统无需安装，双击即可直接运行。由于程序未进行商业代码签名并使用可执行文件压缩，Windows SmartScreen 或部分安全软件可能显示提示，请核对 SHA-256 后再运行。

Download `DogVisionMeasure-v1.1.3-Windows-x64.exe` from **Releases** on the right side of the project page. It is portable and runs directly on Windows without installation. Because it is unsigned and executable compression is enabled, Windows SmartScreen or some security products may warn; verify the SHA-256 before running.

SHA-256: `9BC9161E48A186566A1BF330E73C3E7DAB736F3400324A7DD4CAD6316D4AE2BA`
