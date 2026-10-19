# Update MediaWiki checkouts

`mw dev mediawiki update-code` updates core and checked-out skins and extensions.
Git-only updates and `--dry-run` do not require Docker. Choose a safety mode with
`--mode`; use `--composer` to update PHP dependencies after successful source
updates.

For mode behavior, recovery details, and examples, see
`mw help guides dev update-code`.
