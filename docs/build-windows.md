# Building from source on Windows

The launcher downloads prebuilt Windows releases when available. Building
from source needs a C++ toolchain, CMake, Qt, and the Vulkan SDK. Install
once, then the launcher's dependency check goes green.

## 1. Visual Studio Build Tools (compilers)

Install **Visual Studio 2022 Build Tools** with the **Desktop development
with C++** workload. In the installer details, make sure these components
are included:

- MSVC v143 toolchain and Windows SDK
- C++ Clang tools for Windows (clang-cl)
- C++ CMake tools for Windows (ships CMake and Ninja)

```powershell
winget install Microsoft.VisualStudio.2022.BuildTools
```

Then re-run the installer (`vs_community`/`vs_buildtools` entry in Start)
to add the workload and components above.

Always build from a **Developer Command Prompt** or **Developer PowerShell**
(Start Menu → "Developer ..."), so `cl`, `clang-cl`, `cmake` and `ninja`
are on `PATH`. Verify:

```powershell
cl
cmake --version
ninja --version
```

## 2. Git and Python

```powershell
winget install Git.Git Python.Python.3.12
```

Verify with `git --version` and `python --version` in a new terminal.

## 3. Vulkan SDK

Download the Windows installer from
<https://vulkan.lunarg.com/sdk/home#windows> and run it. It sets the
`VULKAN_SDK` environment variable (e.g. `C:\VulkanSDK\1.4.341.1`).
Confirm it in a new terminal:

```powershell
$env:VULKAN_SDK
glslangValidator --version
```

The Eglantine repo also ships `tools/windows/install-vulkan-sdk.ps1`,
which downloads and installs the SDK for you (run as administrator).

## 4. Qt 6 (MSVC build)

Download the official Qt online installer from
<https://www.qt.io/download-qt-installer-oss>. During installation select:

- Qt 6.8.x → **MSVC 2022 64-bit**
- Qt 6.8.x → **Additional Libraries → Qt Charts** (required module)

Install to e.g. `C:\Qt`, then set a user environment variable:

```powershell
[System.Environment]::SetEnvironmentVariable("QT_ROOT", "C:\Qt\6.8.3\msvc2022_64", "User")
```

Open a new Developer PowerShell afterwards and check:

```powershell
$env:QT_ROOT
```

(Advanced alternative: `pip install aqtinstall`, then
`python -m aqt install-qt windows desktop 6.8.3 win64_msvc2022_64 -m qtcharts`.)

## 5. Build with the launcher

1. Open the launcher, pick a project and **Build from source**.
2. Press **Launch**. Missing pieces pop up in a dialog with a copyable list;
   install whatever it names and try again.

## Notes

- PGO source builds are Linux-only for now. On Windows the launcher
  always does a regular (non-PGO) build, even with the PGO switch on.
- A first build takes 40-60 minutes; later builds only recompile what changed.
- Do not run the emulator while building, and do not replace
  `eglantine.exe` while it is running (the link step will fail with
  "permission denied").
