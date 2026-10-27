# Changelog

All notable changes to FuncFinder are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project follows [Semantic Versioning](https://semver.org/).

## [v0.3.0] - 2026-10-27

### Added

- Color-coded variable scopes: global, local, member, class/instance attribute,
  and type-member results now use distinct badge colors, card accent stripes,
  and matching variable-name highlight colors in the code view.
- Case-sensitivity toggle. It is on by default; turning it off matches `A` and
  `a` equally while results keep the original spelling from the source.
- Case-insensitive searches now use `ripgrep`/`grep` with `-i` when available
  instead of falling back to Python, while still resolving the actual spelling
  from the source line.
- The pure-Python literal scanner now performs a cheap name-token prefilter
  before the full declaration candidate expression, so it no longer evaluates
  that heavier expression on every source line.
- Result sorting by default order, name, file path, scope, or language, with an
  ascending/descending toggle.
- Result filtering by text (name, path, language, or signature; multiple terms
  use AND) and by function/global/local/member/class/instance/type scope. The
  count label shows visible/total results, and Copy all only copies the
  currently visible results.
- A persistent small engine label below the search row. It shows the active
  backend even before a search starts, replacing the engine text that was only
  visible in the search status.
- The Match-case option button now aligns with the search input and
  Search/Stop buttons.
- Unit tests for case sensitivity, grep-backed case-insensitive search,
  filtering, sorting, and scope colors.

### Changed

- Updated README usage and limitations for scope colors, case sensitivity,
  sorting, and filtering.
- Bumped the application version to 0.3.0.

## [v0.2.0] - 2026-10-27

### Added

- Variable/constant definition search alongside function search, with a
  function/variable mode switch in the UI.
- Variable extraction for Python, C/C++, Objective-C, Go, Rust, Java, C#,
  JavaScript/TypeScript, Kotlin, Scala, Swift, and Dart, including struct/class
  fields, Go grouped declarations/short declarations, Rust `let`/`static`/
  `const`, Python `self.attr` assignments, and C++ static member definitions.
- Scope labels for variable results (global, local, member, class/instance
  attribute) and multi-line initializer extraction.
- Search mode is persisted in settings and translated in both UI languages.
- Unit tests for variable extraction and variable/function search modes.

### Changed

- Window title, desktop entry metadata, README, and empty-state text now
  describe both function and variable search.

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

[v0.3.0]: https://github.com/zhijian-yan/FuncFinder/releases/tag/v0.3.0
[v0.2.0]: https://github.com/zhijian-yan/FuncFinder/releases/tag/v0.2.0
[v0.1.0]: https://github.com/zhijian-yan/FuncFinder/releases/tag/v0.1.0
