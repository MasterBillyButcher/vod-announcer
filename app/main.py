"""Main orchestration - one full run of the VOD Announcer.

Pipeline per recording (STRICT ORDER, per project spec):
    download -> verify (size/sha256) -> determine date/title
    -> rename (BS_...) -> validate name -> upload -> store link
Uploading an un-renamed file is structurally impossible: the uploader
receives the validated BS_ name as its display name and the renamed
file path as its input.

Session flow: recordings are grouped per stream day; a session is
announced once after the 60-minute timer (reset by every new recording)
has expired and all parts are uploaded (or permanently failed).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import traceback

from app.config import Config, load_config
from app.discord_notify import build_announcement, send
from app.downloader import DownloadError, download_file
from app.logsetup import get_logger, setup_logging
from app.models import Recording
from app.renamer import build_name, rename_file, validate
from app.savedly import SavedlyClient, UploadError
from app.session_manager import SessionManager
from app.sources.streamrecorder import NoDownloadURLError, StreamRecorderSource
from app.state_manager import StateManager
from app.timeutil import format_duration, now_utc

log = get_logger()


def _cleanup_workdir(work_dir: str) -> None:
    if os.path.isdir(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)


def process_recording(cfg: Config, rec: Recording, source: StreamRecorderSource,
                      uploader: SavedlyClient, part_number: int | None) -> bool:
    """Run download->verify->rename->validate->upload for one recording.

    Returns True when `rec.link` is set afterwards.
    """
    if rec.link:
        return True

    # 1+2) download & verify
    if not rec.filename or not rec.size_bytes:
        url, mode = source.get_download_url(rec)
        if mode == "preview":
            log.warning("recording %s: using ANONYMOUS PREVIEW url (size-capped) - "
                        "set STREAMRECORDER_COOKIE for full downloads", rec.source_id)
        tmp_basename = f"rec_{rec.source_id}.mp4"
        path, size, sha = download_file(
            url, cfg.work_dir, tmp_basename,
            timeout=cfg.download_timeout, retries=cfg.download_retries,
            max_bytes=cfg.max_upload_mb * 1024 * 1024,
        )
        rec.size_bytes = size
        rec.sha256 = sha
        log.info("recording %s: verified download (%s, %s)",
                 rec.source_id, mode, format_duration(rec.duration_seconds))
    else:
        # interrupted previously after download; re-download (runner is ephemeral)
        url, mode = source.get_download_url(rec)
        path, size, sha = download_file(
            url, cfg.work_dir, f"rec_{rec.source_id}.mp4",
            timeout=cfg.download_timeout, retries=cfg.download_retries,
            max_bytes=cfg.max_upload_mb * 1024 * 1024,
        )
        rec.size_bytes, rec.sha256 = size, sha

    # 3) date + title are already determined during discovery (rec.rec_date/title)

    # 4) rename
    final_name = build_name(rec.rec_date, rec.title, part_number)
    rec.filename = final_name

    # 5) validate
    if not validate(final_name):
        raise RuntimeError(f"generated name failed validation: {final_name!r}")
    final_path = rename_file(path, final_name)
    log.info("recording %s: renamed -> %s%s", rec.source_id, final_name,
             os.path.splitext(final_path)[1])

    # 6+7) upload & store link
    rec.status = "uploading"
    display_name = final_name + os.path.splitext(final_path)[1]
    result = uploader.upload_file(final_path, display_name)
    rec.link = result["url"]
    rec.delete_token = result.get("deleteToken", "")
    rec.status = "uploaded"
    log.info("recording %s: uploaded -> %s", rec.source_id, rec.link)
    return True


def run(cfg: Config) -> int:
    setup_logging()
    problems = cfg.validate()
    if problems:
        for p in problems:
            log.error("config problem: %s", p)
        return 2

    log.info("VOD Announcer starting | streamers=%s wait=%dmin backlog=%dd dry_run=%s",
             ",".join(cfg.streamers), cfg.wait_minutes, cfg.backlog_days, cfg.dry_run)

    state = StateManager(cfg.state_path)
    sessions = SessionManager(state, cfg)
    source = StreamRecorderSource(cfg)
    uploader = SavedlyClient(cfg.savedly_base, cfg.user_agent,
                             cfg.http_timeout, cfg.part_retries)

    if cfg.diagnose:
        for channel in cfg.streamers:
            report = source.diagnose(channel)
            log.info("DIAGNOSE %s", json.dumps(report))
        state.save()
        return 0

    # ------------------------------------------------------- discovery
    new_count = 0
    for channel in cfg.streamers:
        try:
            recordings = source.list_recordings(channel)
        except Exception as exc:  # noqa: BLE001 - keep other channels alive
            log.error("discovery failed for %s: %s", channel, exc)
            continue
        for rec in recordings:
            if state.is_new(rec.source_id):
                state.add_recording(rec)
                try:
                    sessions.register(rec)
                except Exception as exc:  # noqa: BLE001 - one bad record must not kill the run
                    log.error("could not register recording %s: %s", rec.source_id, exc)
                    continue
                new_count += 1
                log.info("NEW recording %s | %s | %s | %s",
                         rec.source_id, rec.rec_date, rec.title, format_duration(rec.duration_seconds))
            else:
                existing = state.get_recording(rec.source_id)
                if existing:
                    # refresh the preview url if we now have one
                    if rec.preview_url and not existing.preview_url:
                        existing.preview_url = rec.preview_url
                        state.update_recording(existing)
    log.info("discovery finished: %d new recording(s)", new_count)

    if cfg.dry_run:
        state.save()
        _report(state, sessions)
        return 0

    # ------------------------------------------- announcement processing
    try:
        due = sessions.due_sessions()
    except Exception as exc:  # noqa: BLE001 - never let a state quirk kill the run
        log.error("could not compute due sessions: %s", exc)
        due = []
    announced = 0

    def _given_up(rec: Recording) -> bool:
        return rec.status == "blocked" or (
            rec.attempts >= cfg.max_recording_attempts and not rec.link
        )

    for session in due:
        summary = sessions.session_summary(session)
        recordings = summary["recordings"]
        ok_parts: list[dict] = []
        warnings: list[str] = []
        pending = 0
        for idx, rec in enumerate(recordings, 1):
            part_no = idx if len(recordings) > 1 else None
            if rec.link:
                ok_parts.append({
                    "filename": rec.filename or rec.source_id,
                    "duration_seconds": rec.duration_seconds,
                    "link": rec.link,
                })
                continue
            if rec.status == "blocked":
                warnings.append(
                    f"\u26A0\uFE0F Part {idx} ({rec.rec_date}) is not downloadable yet - "
                    "it needs the STREAMRECORDER_COOKIE secret (see docs/DEPLOYMENT.md)"
                )
                continue
            try:
                rec.attempts += 1
                rec.status = "downloading"
                state.update_recording(rec)
                state.save()
                process_recording(cfg, rec, source, uploader, part_no)
                state.update_recording(rec)  # persist link/status after success
                ok_parts.append({
                    "filename": rec.filename or rec.source_id,
                    "duration_seconds": rec.duration_seconds,
                    "link": rec.link,
                })
            except NoDownloadURLError as exc:
                rec.status = "blocked"
                rec.last_error = str(exc)[:300]
                state.update_recording(rec)
                log.warning("recording %s is blocked (no public download URL): %s",
                            rec.source_id, exc)
                warnings.append(
                    f"\u26A0\uFE0F Part {idx} ({rec.rec_date}) is not downloadable yet - "
                    "it needs the STREAMRECORDER_COOKIE secret (see docs/DEPLOYMENT.md)"
                )
            except (DownloadError, UploadError, RuntimeError, OSError) as exc:
                rec.last_error = str(exc)[:300]
                state.update_recording(rec)
                if _given_up(rec):
                    warnings.append(f"\u26A0\uFE0F Part {idx} ({rec.rec_date}) failed permanently: {rec.last_error}")
                    log.error("recording %s gave up after %d attempts: %s",
                              rec.source_id, rec.attempts, exc)
                else:
                    pending += 1
                    log.warning("recording %s attempt %d failed (will retry next run): %s",
                                rec.source_id, rec.attempts, exc)
            except Exception as exc:  # noqa: BLE001 - unexpected bug: log, keep state, retry next run
                rec.last_error = f"{type(exc).__name__}: {exc}"[:300]
                state.update_recording(rec)
                pending += 1
                log.error("recording %s unexpected error (will retry next run): %s",
                          rec.source_id, rec.last_error)
            state.save()

        if not ok_parts:
            if not pending:
                log.error("session %s has no downloadable parts and will NOT be announced "
                          "- set STREAMRECORDER_COOKIE to unlock full downloads", session.key)
            else:
                log.info("session %s: nothing uploaded yet, will retry later", session.key)
            continue

        if pending:
            log.info("session %s: %d part(s) ready but %d still pending, waiting",
                     session.key, len(ok_parts), pending)
            state.save()
            continue

        base_title = ok_parts[0]["filename"]
        if len(ok_parts) > 1 and "_P" in base_title:
            base_title = base_title.rsplit("_P", 1)[0]
        try:
            messages = build_announcement(
                channel=session.channel,
                rec_date=session.rec_date,
                title=base_title or (recordings[0].title or session.rec_date),
                parts=ok_parts,
                contact=cfg.contact,
                warnings=warnings,
            )
            message_url = send(cfg.discord_webhook_url, messages)
            sessions.mark_announced(session, message_url)
            announced += 1
            log.info("session %s ANNOUNCED with %d part link(s)", session.key, len(ok_parts))
        except Exception as exc:  # noqa: BLE001
            log.error("discord announcement failed for %s: %s", session.key, exc)
        state.save()

    state.save()
    _cleanup_workdir(cfg.work_dir)
    log.info("run complete: %d new, %d session(s) announced", new_count, announced)
    return 0


def _report(state: StateManager, sessions: SessionManager) -> None:
    log.info("--- DRY RUN REPORT ---")
    for raw in state.data["sessions"].values():
        s = sessions.state.get_session(raw.get("key", ""))
        recs = state.recordings_in_session(s)
        log.info("session %s | parts=%d | announced=%s", s.key, len(recs), s.announced)
        for r in recs:
            log.info("   %s | %s | %s | dur=%s | status=%s",
                     r.source_id, r.rec_date, r.title, format_duration(r.duration_seconds), r.status)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cfg = load_config()
    if "--diagnose" in argv:
        cfg.diagnose = True
    if "--dry-run" in argv:
        cfg.dry_run = True
    try:
        return run(cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("FATAL: %s", exc)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
