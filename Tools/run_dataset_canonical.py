#!/usr/bin/env python3
"""Render tabletop videos and generate QA solely from canonical v7 JSON."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import regenerate_existing_qa_canonical as canonical_qa


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESOLUTIONS = (("CLIP", 336), ("DFN", 378), ("SigLIP", 384))
active_processes: set[subprocess.Popen[bytes]] = set()
process_lock = threading.Lock()
stop_event = threading.Event()


def env_text(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int, *, positive: bool = False) -> int:
    raw = env_text(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer: {raw!r}") from error
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive: {value}")
    return value


def env_bool(name: str, default: bool) -> bool:
    raw = env_text(name, "1" if default else "0").strip().lower()
    if raw not in {"0", "1", "false", "true", "no", "yes"}:
        raise ValueError(f"{name} must be 0/1 or false/true: {raw!r}")
    return raw in {"1", "true", "yes"}


@dataclass(frozen=True)
class Config:
    player: Path
    output: Path
    canonical_dir: Path
    model_bundle_dir: Path
    unity_config_dir: Path
    start_index: int
    count: int
    fps: int
    duration: int
    width: int
    height: int
    random_resolution: bool
    resolution_seed: int
    seed: str
    use_xvfb: bool
    display_width: int
    display_height: int
    progress_interval: int
    force_change_type: str
    force_changed_slot: str
    clean_output: bool
    delete_frames: bool
    crf: int
    preset: str
    ffmpeg_loglevel: str
    ffmpeg_threads: int
    resume: bool
    workers: int
    unity_job_workers: int
    clean_item_config: bool
    questions_per_scene: int
    sampling_salt: int


def parse_args() -> tuple[argparse.Namespace, Config]:
    parser = argparse.ArgumentParser(
        description=(
            "Render Unity tabletop videos, encode them with ffmpeg, then "
            "generate structured QA from canonical/QAs_v7_*.json only. "
            "Dataset settings use the same environment variables as the "
            "legacy runner."
        )
    )
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    random_start = env_bool("RANDOM_START", False)
    start_was_set = "START_INDEX" in os.environ
    start_index = env_int("START_INDEX", 0)
    if random_start and not start_was_set:
        start_index = random.randint(100000, 899999)
    config = Config(
        player=Path(env_text("PLAYER", str(PROJECT_ROOT / "Build/Linux/ChangeBlindnessRoom.x86_64"))).expanduser().resolve(),
        output=Path(env_text("OUTPUT", str(PROJECT_ROOT / "outz"))).expanduser().resolve(),
        canonical_dir=Path(env_text("CANONICAL_QA_DIR", str(PROJECT_ROOT.parent / "canonical"))).expanduser().resolve(),
        model_bundle_dir=Path(env_text("MODEL_BUNDLE_DIR", str(PROJECT_ROOT / "ModelBundles"))).expanduser().resolve(),
        unity_config_dir=Path(env_text("UNITY_CONFIG_DIR", str(PROJECT_ROOT / ".unity_config_canonical"))).expanduser().resolve(),
        start_index=start_index,
        count=env_int("COUNT", 24, positive=True),
        fps=env_int("FPS", 30, positive=True),
        duration=env_int("CAPTURE_DURATION_SECONDS", 31, positive=True),
        width=env_int("WIDTH", 384, positive=True),
        height=env_int("HEIGHT", 384, positive=True),
        random_resolution=env_bool("RANDOM_RESOLUTION", True),
        resolution_seed=env_int("RESOLUTION_SEED", 20260718),
        seed=env_text("SEED", "").strip(),
        use_xvfb=env_bool("USE_XVFB", True),
        display_width=env_int("DISPLAY_WIDTH", 960, positive=True),
        display_height=env_int("DISPLAY_HEIGHT", 540, positive=True),
        progress_interval=env_int("PROGRESS_INTERVAL", 10, positive=True),
        force_change_type=env_text("FORCE_CHANGE_TYPE", "").strip(),
        force_changed_slot=env_text("FORCE_CHANGED_SLOT", "").strip(),
        clean_output=env_bool("CLEAN_OUTPUT", False),
        delete_frames=env_bool("DELETE_FRAMES", True),
        crf=env_int("CRF", 16),
        preset=env_text("PRESET", "medium"),
        ffmpeg_loglevel=env_text("FFMPEG_LOGLEVEL", "warning"),
        ffmpeg_threads=env_int("FFMPEG_THREADS", 2, positive=True),
        resume=env_bool("RESUME", False),
        workers=env_int("WORKERS", 2, positive=True),
        unity_job_workers=env_int("UNITY_JOB_WORKERS", 2, positive=True),
        clean_item_config=env_bool("CLEAN_ITEM_CONFIG", True),
        questions_per_scene=env_int("QUESTIONS_PER_SCENE", 8, positive=True),
        sampling_salt=env_int("SAMPLING_SALT", 20260726),
    )
    return args, config


def require_file(path: Path, message: str) -> None:
    if not path.is_file():
        raise ValueError(f"{message}: {path}")


def validate(config: Config) -> None:
    canonical_qa.load_canonical_libraries(config.canonical_dir)
    require_file(config.player, "Unity Player not found")
    if not os.access(config.player, os.X_OK):
        raise ValueError(f"Unity Player is not executable: {config.player}")
    require_file(
        config.model_bundle_dir / "prop_manifest.json",
        "Model bundle manifest not found",
    )
    for command in (["ffmpeg"] + (["xvfb-run"] if config.use_xvfb else [])):
        if shutil.which(command) is None:
            raise ValueError(f"Required command is not installed: {command}")


def resolution(config: Config, index: int) -> tuple[str, int, int]:
    if not config.random_resolution:
        return "custom", config.width, config.height
    mixed = ((index ^ config.resolution_seed) * 1103515245 + 12345) & 0x7FFFFFFF
    mixed = (mixed ^ (mixed >> 16)) & 0x7FFFFFFF
    name, size = RESOLUTIONS[mixed % len(RESOLUTIONS)]
    return name, size, size


def batch_directories(output: Path, index: int) -> list[Path]:
    return sorted(
        output.glob(f"Batch_{index:06d}_*"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def register(process: subprocess.Popen[bytes]) -> None:
    with process_lock:
        active_processes.add(process)


def unregister(process: subprocess.Popen[bytes]) -> None:
    with process_lock:
        active_processes.discard(process)


def terminate_all() -> None:
    stop_event.set()
    with process_lock:
        processes = list(active_processes)
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def run_process(command: list[str], *, log: Path | None = None) -> int:
    destination = log.open("wb") if log else subprocess.DEVNULL
    try:
        process = subprocess.Popen(
            command,
            stdout=destination,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        register(process)
        return process.wait()
    finally:
        if "process" in locals():
            unregister(process)
        if log and destination is not subprocess.DEVNULL:
            destination.close()


def encode(config: Config, index: int, batch_dir: Path) -> None:
    frames = batch_dir / "frames"
    first_frame = frames / "frame_000000.png"
    if not first_frame.is_file():
        raise RuntimeError(f"No PNG sequence found: {frames}")
    video = config.output / "data" / f"video_{index:06d}.mp4"
    temporary = video.with_name(f".{video.name}.tmp.{os.getpid()}.{threading.get_ident()}.mp4")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", config.ffmpeg_loglevel,
        "-y", "-framerate", str(config.fps), "-i", str(frames / "frame_%06d.png"),
        "-c:v", "libx264", "-threads", str(config.ffmpeg_threads),
        "-preset", config.preset, "-crf", str(config.crf), "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(temporary),
    ]
    status = run_process(command)
    if status != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg failed for item {index} with exit code {status}")
    os.replace(temporary, video)
    if config.delete_frames:
        shutil.rmtree(frames)


def render_one(config: Config, index: int, ordinal: int) -> str:
    if stop_event.is_set():
        raise RuntimeError("cancelled")
    video = config.output / "data" / f"video_{index:06d}.mp4"
    if config.resume and video.is_file() and video.stat().st_size > 0:
        directories = batch_directories(config.output, index)
        if not directories:
            raise RuntimeError(
                f"Existing video has no Batch directory for item {index}"
            )
        canonical_qa.write_one_canonical_batch(
            output_root=config.output,
            batch_dir=directories[0],
            libraries=canonical_qa.load_canonical_libraries(
                config.canonical_dir
            ),
            questions_per_scene=config.questions_per_scene,
            sampling_salt=config.sampling_salt,
        )
        return (
            f"[item {index}][{ordinal}/{config.count}] resumed existing "
            "video and replaced its QA with canonical v7"
        )
    profile, width, height = resolution(config, index)
    item_config = config.unity_config_dir / "jobs" / f"item_{index:06d}"
    if item_config.exists():
        shutil.rmtree(item_config)
    (item_config / "unity3d/ChangeBlindness/ChangeBlindnessRoom").mkdir(parents=True)
    log = config.output / "logs" / f"batch_{index:06d}.log"
    player_args = [
        str(config.player), "-batchmode", "-job-worker-count", str(config.unity_job_workers),
        "-screen-fullscreen", "0", "-screen-width", str(config.display_width),
        "-screen-height", str(config.display_height), "-logFile", str(log),
        "--batch-index", str(index), "--capture", "--auto-quit", "--fps", str(config.fps),
        "--width", str(width), "--height", str(height), "--output", str(config.output),
        "--model-bundle-dir", str(config.model_bundle_dir),
    ]
    if config.seed:
        player_args += ["--seed", config.seed]
    if config.force_change_type:
        player_args += ["--change-type", config.force_change_type]
    if config.force_changed_slot:
        player_args += ["--changed-slot", config.force_changed_slot]
    command = player_args
    if config.use_xvfb:
        command = [
            "xvfb-run", "-a", "-s",
            f"-screen 0 {config.display_width}x{config.display_height}x24",
            *player_args,
        ]
    environment = os.environ.copy()
    environment["XDG_CONFIG_HOME"] = str(item_config)
    with log.open("wb") as output:
        process = subprocess.Popen(
            command, stdout=output, stderr=subprocess.STDOUT,
            env=environment, start_new_session=True,
        )
        register(process)
        try:
            while process.poll() is None:
                if stop_event.wait(config.progress_interval):
                    os.killpg(process.pid, signal.SIGTERM)
                    break
            status = process.wait()
        finally:
            unregister(process)
    if status != 0:
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        raise RuntimeError(
            f"Unity failed for item {index} with exit code {status}\n" + "\n".join(tail)
        )
    directories = batch_directories(config.output, index)
    if not directories:
        raise RuntimeError(f"Unity created no Batch directory for item {index}")
    batch_dir = directories[0]
    frame_count = sum(1 for _ in (batch_dir / "frames").glob("frame_*.png"))
    expected = config.duration * config.fps
    if frame_count < expected:
        raise RuntimeError(
            f"Incomplete item {index}: expected {expected} frames, found {frame_count}"
        )
    # Unity's currently built Player writes legacy string-answer QA alongside
    # the raw scene annotation. Remove those visible legacy derivatives before
    # encoding, then replace the annotation itself immediately after the MP4
    # succeeds. Only the scene-state fields from annotation.json are retained.
    (batch_dir / "qa_entries.json").unlink(missing_ok=True)
    (batch_dir / "qa.txt").unlink(missing_ok=True)
    annotation_path = batch_dir / "annotation.json"
    raw_annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    raw_annotation.pop("qa", None)
    raw_annotation.pop("qaTemplateIds", None)
    raw_annotation.pop("qaSchemaVersion", None)
    canonical_qa.atomic_json_write(annotation_path, raw_annotation)
    encode(config, index, batch_dir)
    libraries = canonical_qa.load_canonical_libraries(config.canonical_dir)
    canonical_qa.write_one_canonical_batch(
        output_root=config.output,
        batch_dir=batch_dir,
        libraries=libraries,
        questions_per_scene=config.questions_per_scene,
        sampling_salt=config.sampling_salt,
    )
    if config.clean_item_config and item_config.exists():
        shutil.rmtree(item_config)
    return f"[item {index}][{ordinal}/{config.count}] complete ({profile} {width}x{height})"


def generate_canonical_qa(config: Config) -> int:
    old_argv = sys.argv
    try:
        sys.argv = [
            str(Path(canonical_qa.__file__)), str(config.output),
            "--canonical-dir", str(config.canonical_dir),
            "--questions-per-scene", str(config.questions_per_scene),
            "--sampling-salt", str(config.sampling_salt),
            "--no-backup", "--require-all-videos",
        ]
        return canonical_qa.main()
    finally:
        sys.argv = old_argv


def main() -> int:
    try:
        args, config = parse_args()
        validate(config)
    except ValueError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    print(
        f"Validated 8 canonical QA files; XLSX templates are not read.\n"
        f"Output: {config.output}\n"
        f"Items: {config.start_index}..{config.start_index + config.count - 1}\n"
        f"Workers: {min(config.workers, config.count)}"
    )
    if args.validate_only:
        return 0
    if config.clean_output and config.output.exists():
        shutil.rmtree(config.output)
    (config.output / "data").mkdir(parents=True, exist_ok=True)
    (config.output / "logs").mkdir(parents=True, exist_ok=True)
    (config.unity_config_dir / "jobs").mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    workers = min(config.workers, config.count)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(render_one, config, index, ordinal): index
                for ordinal, index in enumerate(
                    range(config.start_index, config.start_index + config.count), 1
                )
            }
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                try:
                    print(future.result(), flush=True)
                    completed += 1
                    print(f"[overall] {completed}/{config.count}", flush=True)
                except Exception as error:
                    terminate_all()
                    print(str(error), file=sys.stderr)
                    for pending in futures:
                        pending.cancel()
                    return 1
    except KeyboardInterrupt:
        terminate_all()
        return 130
    qa_status = generate_canonical_qa(config)
    if qa_status != 0:
        return qa_status
    elapsed = int(time.monotonic() - started)
    print(
        f"Dataset complete in {elapsed // 60}m {elapsed % 60}s.\n"
        f"Videos: {config.output / 'data'}\n"
        f"Canonical QA: {config.output / 'videodata.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
