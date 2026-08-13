# 狗狗测量 v1.0.0 / Dog Measure v1.0.0

首个公开版本。/ First public release.

## 主要功能 / Highlights

- 自动识别圆并给出直径与圆心，无需人工寻找圆心。
- 自动排除已测圆，支持相邻同心圆与同心度测量。
- 从三个已检测圆自动绘制圆周阵列外接圆并计算直径。
- 自动识别常见公制标尺，支持两点手动标定。
- 自动吸附边缘和角点，提供完整二维测量工具。
- UVC 摄像头实时测量与同名 PNG + CSV 一键导出。

- Automatically detects circles and reports diameter and center coordinates without manual center picking.
- Excludes previously measured circles for nearby concentric-ring and concentricity measurement.
- Builds a circular array from three detected circle centers and reports its diameter.
- Detects common metric rulers automatically, with two-point manual calibration as fallback.
- Snaps to nearby edges and corners and provides a complete planar measurement toolset.
- Supports live UVC measurement and one-click same-named PNG + CSV export.

## Windows 免安装版 / Portable Build

下载 `狗狗测量.exe` 后双击运行。由于程序未进行商业代码签名，Windows SmartScreen 可能显示提示。

Download `狗狗测量.exe` and run it directly. Windows SmartScreen may display a warning because this build is not commercially code-signed.
