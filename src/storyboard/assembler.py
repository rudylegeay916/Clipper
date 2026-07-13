"""Technical helpers for storyboard assembly and subtitle mapping."""

from __future__ import annotations

from pathlib import Path

from src.utils.ffmpeg import run_ffmpeg


def build_output_timeline(segments: list[dict]) -> list[dict]:
    output = []
    cursor = 0.0
    for segment in sorted(segments, key=lambda item: float(item["source_start_seconds"])):
        start = float(segment["source_start_seconds"])
        end = float(segment["source_end_seconds"])
        duration = max(0.0, end - start)
        output.append({
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "output_start": round(cursor, 3),
            "output_end": round(cursor + duration, 3),
            "source_start_seconds": round(start, 3),
            "source_end_seconds": round(end, 3),
            "output_start_seconds": round(cursor, 3),
            "output_end_seconds": round(cursor + duration, 3),
            "source_text": segment.get("source_text", ""),
            "role": segment.get("role", "evidence"),
        })
        cursor += duration
    return output


def map_word_to_output_time(word: dict, output_timeline: list[dict]) -> dict | None:
    word_start = float(word["start"])
    word_end = float(word["end"])
    for segment in output_timeline:
        source_start = float(segment.get("source_start", segment.get("source_start_seconds")))
        source_end = float(segment.get("source_end", segment.get("source_end_seconds")))
        if word_end <= source_start or word_start >= source_end:
            continue
        output_start = float(segment.get("output_start", segment.get("output_start_seconds")))
        start = output_start + max(word_start, source_start) - source_start
        end = output_start + min(word_end, source_end) - source_start
        if end - start < 0.02:
            return None
        return {
            "word": word["word"],
            "start": round(start, 3),
            "end": round(end, 3),
            "absolute_start": round(word_start, 3),
            "absolute_end": round(word_end, 3),
        }
    return None


def map_words_to_output_timeline(words: list[dict], output_timeline: list[dict]) -> list[dict]:
    mapped = [map_word_to_output_time(word, output_timeline) for word in words]
    return [word for word in mapped if word is not None]


def assemble_story_clip(video_path: Path, segments: list[dict], destination: Path,
                        has_audio: bool = True, encode_crf: int = 20,
                        encode_preset: str = "veryfast") -> dict:
    """Cut each source segment and concatenate with normalized PTS."""
    ordered = sorted(segments, key=lambda item: float(item["source_start_seconds"]))
    if len(ordered) < 2:
        raise ValueError("Un assemblage multi_scene requiert au moins deux segments.")

    args: list[object] = []
    for segment in ordered:
        start = float(segment["source_start_seconds"])
        duration = float(segment["source_end_seconds"]) - start
        if duration <= 0:
            raise ValueError("Segment multi_scene de duree invalide.")
        args.extend(["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", video_path])

    filters = []
    inputs = []
    for index, _segment in enumerate(ordered):
        filters.append(f"[{index}:v]setpts=PTS-STARTPTS,format=yuv420p[v{index}]")
        inputs.append(f"[v{index}]")
        if has_audio:
            filters.append(f"[{index}:a]asetpts=PTS-STARTPTS,aresample=48000[a{index}]")
            inputs.append(f"[a{index}]")
    concat = "".join(inputs) + f"concat=n={len(ordered)}:v=1:a={1 if has_audio else 0}"
    concat += "[v][a]" if has_audio else "[v]"
    filters.append(concat)

    args.extend([
        "-filter_complex", ";".join(filters),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", encode_preset, "-crf", encode_crf,
        "-pix_fmt", "yuv420p",
    ])
    if has_audio:
        args.extend(["-map", "[a]", "-c:a", "aac", "-b:a", "192k"])
    args.extend(["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", destination])
    run_ffmpeg(args)

    timeline = build_output_timeline(ordered)
    return {
        "method": "multi_scene_concat",
        "actual_start": round(float(ordered[0]["source_start_seconds"]), 3),
        "actual_end": round(float(ordered[-1]["source_end_seconds"]), 3),
        "duration": round(sum(float(s["source_end_seconds"]) - float(s["source_start_seconds"]) for s in ordered), 3),
        "timeline_segments": timeline,
    }
