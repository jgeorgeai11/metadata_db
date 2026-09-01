# Launches pgweb — a read-only web UI over metadata_db for query/BI users —
# in multi-session bookmark mode.
#
# --sessions        one DB connection per browser session (viewers don't share,
#                   so one person's disconnect or slow query affects only them)
# --bookmarks-only  the connection form is a pick-list of bookmarks/; hand-typed
#                   connections are refused, so viewers can only reach what a
#                   bookmark names
# --readonly        pgweb-level guard on top of pgweb_ro's SELECT-only grants
#                   (see code/apply_ddl/grants/pgweb_ro.sql for the role model)
#
# Viewers connect by clicking the "metadata_db" bookmark; its credentials live
# only in bookmarks/metadata_db.toml (gitignored — copy the .example and fill
# in the password). Restarts an already-running instance, so this is also the
# "bounce it" script.
#
# The pgweb binary itself is not vendored (an 18 MB Go executable): download
# pgweb_windows_amd64.zip from https://github.com/sosedoff/pgweb/releases,
# unzip, and set $PGWEB_EXE (or keep the default path below).
#
# Port 8001 sits between the policy MCP instance (8000) and the metadata MCP
# instance (8002); bound 0.0.0.0 so other machines can reach it. There is no
# HTTP auth, matching the trusted-host stance of the MCP instances — the blast
# radius is capped by the role's DB-enforced read-only grants.
$pgwebExe = if ($env:PGWEB_EXE) { $env:PGWEB_EXE } else { "$HOME\tools\pgweb\pgweb.exe" }
$bookmarksDir = Join-Path $PSScriptRoot 'bookmarks'
Get-Process pgweb -ErrorAction SilentlyContinue | Stop-Process -Force -Confirm:$false
Start-Process -FilePath $pgwebExe `
    -ArgumentList '--sessions', '--bookmarks-only', '--readonly', '--skip-open', `
        '--bookmarks-dir', $bookmarksDir, '--bind', '0.0.0.0', '--listen', '8001' `
    -WindowStyle Hidden
Write-Host "pgweb starting at http://localhost:8001 (multi-session, bookmarks-only, read-only)"
