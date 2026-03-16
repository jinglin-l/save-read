# save-read

Save web content as markdown to your Obsidian vault/somewhere locally. Supports blogs, Twitter/X, Hacker News threads, and Reddit threads.

## Setup

```bash
# clone and create venv
cd ~/code/save-read
python3 -m venv venv
venv/bin/pip install readability-lxml markdownify requests beautifulsoup4 playwright
venv/bin/playwright install chromium
```

Edit `SAVE_DIR` in `save-read.py` to point to your desired output folder.

### Shell alias

**fish:**
```fish
abbr -a save-read ~/code/save-read/save-read
```

**zsh/bash** (add to `~/.zshrc` or `~/.bashrc`):
```bash
alias save-read="~/code/save-read/save-read"
```

## Usage

```bash
save-read <url> [--tags tag1 tag2]
```

### Examples

```bash
save-read https://x.com/karpathy/status/123456789 --tags ai ml
save-read https://news.ycombinator.com/item?id=12345
```

Files are saved as `YYYY-MM-DD-slugified-title.md` with YAML frontmatter (title, url, source, date, tags).
