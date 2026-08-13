# 狗狗测量 v1.1.0 / Dog Measure v1.1.0

新增圆心距与画面多选功能。/ Adds center-distance measurement and canvas multi-selection.

**操作视频 / Operation Video:** `DogMeasure-v1.1.0-Operation-Video.mp4`

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

- Automatically detects circles and reports diameter and center coordinates without manual center picking.
- Measures the distance between two detected circle centers with one action.
- Center-distance and three-circle-array workflows accept direct canvas selection or result-list selection.
- Canvas multi-selection and result-list highlighting stay synchronized.
- Excludes previously measured circles for nearby concentric-ring and concentricity measurement.
- Builds a circular array from three detected circle centers and reports its diameter.
- Detects common metric rulers automatically, with two-point manual calibration as fallback.
- Snaps to nearby edges and corners and provides a complete planar measurement toolset.
- Supports live UVC measurement and one-click same-named PNG + CSV export.

## Windows 免安装版 / Portable Build

在项目页面右侧 **Releases** 下下载 `DogMeasure-v1.1.0-Windows-x64.exe`，Windows 系统无需安装，双击即可直接运行。由于程序未进行商业代码签名，Windows SmartScreen 可能显示提示。

Download `DogMeasure-v1.1.0-Windows-x64.exe` from **Releases** on the right side of the project page. It is portable and runs directly on Windows without installation. Windows SmartScreen may display a warning because this build is not commercially code-signed.
