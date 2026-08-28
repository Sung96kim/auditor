# assets

## Files

- `icon.svg`: the auditor project icon.
- `claude.svg`: mono Claude mark, `fill="currentColor"`, 24x24 viewBox.
- `codex.svg`: mono Codex mark, `fill="currentColor"`, 24x24 viewBox.
- `claude-color.svg`: brand-color Claude mark, 24x24 viewBox.
- `codex-color.svg`: brand-color Codex mark, 24x24 viewBox.

## Source

- Package: `@lobehub/icons-static-svg`, version 1.94.0.
- Project: https://github.com/lobehub/lobe-icons
- License: MIT (see `LICENSE-lobe-icons` in this directory, vendored from
  https://github.com/lobehub/lobe-icons).
- The marks themselves are trademarks of Anthropic (Claude) and OpenAI (Codex), used here only to
  identify which runner a UI element refers to. The MIT license covers the SVG package, not the
  trademarks.
- Alternative CC0 source for the Claude symbol: the "Claude AI symbol" on Wikimedia Commons,
  https://commons.wikimedia.org/wiki/File:Claude_AI_symbol.svg, CC0 1.0, 100x100 viewBox. Not
  vendored here; the lobe-icons mono/color pair covers both use cases already.

## Changes from upstream

Each file is copied verbatim from the package except:

- Added `role="img"` and `aria-label` (`"Claude"` or `"Codex"`) on the root `<svg>` element.
- Dropped the inline `style="flex:none;line-height:1"` attribute (a lobe-icons layout convenience,
  not part of the mark).
- Kept the existing `<title>` element.

## Which variant to use where

- Mono (`claude.svg`, `codex.svg`): the live page and any themed UI. `fill="currentColor"`
  inherits the surrounding text color, so it works in both light and dark themes.
- Color (`claude-color.svg`, `codex-color.svg`): markdown (README, docs). `currentColor` inside an
  `<img>` renders black and disappears on GitHub's dark theme, so markdown needs the color
  variant instead.
