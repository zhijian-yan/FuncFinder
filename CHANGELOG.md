# Changelog

All notable changes to FuncFinder are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project follows [Semantic Versioning](https://semver.org/).

## [v0.1.0] - 2026-10-26

### Added

- Initial public release of FuncFinder.
- Search function definitions and full implementations across mixed-language codebases.
- Support for C/C++, Python, Go, Rust, Java, C#, JavaScript/TypeScript,
  Kotlin, Scala, Swift, Dart, and Objective-C (C functions).
- Search backends: ripgrep, GNU/BSD grep, and a pure-Python fallback.
- Background search with progress, stop support, incremental result rendering,
  and syntax-highlighted result cards.
- Copy one result, copy all results, light/dark themes, and Chinese/English UI.
- Linux desktop integration: automatic `.desktop` entry and hicolor icon
  installation, plus `--install-desktop` / `--uninstall-desktop`.
- PyInstaller packaging for Linux and Windows with bundled ripgrep.
- GitHub Actions CI and tag-based Linux/Windows release workflow.

### Notes

- PyInstaller cannot cross-compile; Linux and Windows packages must be built on
  their respective platforms (the release workflow does this automatically).
- Function extraction is heuristic and may need manual review for complex
  macros, templates, overloads, or nested definitions.

[v0.1.0]: https://github.com/zhijian-yan/FuncFinder/releases/tag/v0.1.0
