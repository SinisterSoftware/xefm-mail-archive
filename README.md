# Mail Archive

XeFM extension to browse `.eml` and `.msg` files read-only as virtual folders.

Install with `xefm ext install github:SinisterSoftware/xefm-mail-archive`.

After installation, press Enter on an `.eml` or `.msg` file to open its
read-only virtual folder; this works without configuring External Viewers.
You can also open a mail archive directly with XeFM's **Jump to Path** action
(`Ctrl+L` in the OS-native keymap, `Shift+J` in AFX, or `g p` in Vim style):

```text
eml://C:\path\to\message.eml#
msg://C:\path\to\message.msg#
```

`.eml` support uses Python's standard library. Reading `.msg` files requires
the `extract-msg` Python package, declared as an extension dependency and
installed by XeFM when syncing the extension's Python packages.

Other extensions can preserve their own mail-view action by checking
`"mail_archive.open" in ctx.action_names()` and invoking it with
`ctx.invoke("mail_archive.open")`. The action opens only the focused `.eml` or
`.msg` file; it leaves other entries, directories, and an empty pane alone.

For development, run `xefm ext check .` and `python -m pytest tests`.
