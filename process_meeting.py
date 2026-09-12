#!/usr/bin/env python3
"""
Meeting Documentation Engine (Phase 1)
Extracts keyframes from meeting video, computes active slide intervals,
and compiles a clean, chronologically interleaved Markdown document with transcripts.
"""

import os
import re
import json
import argparse
import cv2
import imagehash
from PIL import Image

def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS string."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"

def format_file_timestamp(seconds: float) -> str:
    """Format seconds for filesystem-safe naming."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:02d}h{mins:02d}m{secs:02d}s"
    return f"{mins:02d}m{secs:02d}s"

def parse_vtt(vtt_path: str):
    """
    Parses WebVTT files, supporting both standard Teams transcripts
    (with speaker tags like <v Alice> or 'Alice:') and YouTube rolling captions.
    """
    with open(vtt_path, "r", encoding="utf-8") as f:
        content = f.read()

    cue_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})[^\n]*\n([\s\S]*?)(?=\n\d{2}:\d{2}:\d{2}\.\d{3}|\Z)'
    )
    cues = cue_pattern.findall(content)

    def to_sec(time_str: str) -> float:
        parts = time_str.split(":")
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

    parsed_cues = []
    prev_text = ""

    for start_str, end_str, raw_text in cues:
        start_sec = to_sec(start_str)
        end_sec = to_sec(end_str)

        # Ignore zero/micro-duration flash lines from rolling captions
        if end_sec - start_sec < 0.1:
            continue

        # Extract speaker if available (<v Speaker Name> or Speaker:)
        speaker = None
        speaker_match = re.search(r'<v\s+([^>]+)>', raw_text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            raw_text = re.sub(r'</?v[^>]*>', '', raw_text)

        # Clean tags (e.g. <00:00:02.280><c>, &nbsp;, etc.)
        clean = re.sub(r'<[^>]+>', '', raw_text)
        clean = re.sub(r'&[a-zA-Z]+;', ' ', clean)
        lines = [line.strip() for line in clean.split('\n') if line.strip()]

        if not lines:
            continue

        # Handle inline speaker prefix "Speaker Name: text"
        if not speaker and ":" in lines[0]:
            prefix, rest = lines[0].split(":", 1)
            # If prefix looks like a name (short, no punctuation)
            if len(prefix) < 35 and not any(p in prefix for p in [".", ",", "?", "!"]):
                speaker = prefix.strip()
                lines[0] = rest.strip()

        # Handle rolling/karaoke captions where previous line is repeated
        if len(lines) > 1 and lines[0] in prev_text:
            text = lines[1]
        else:
            text = " ".join(lines)

        text = re.sub(r'\s+', ' ', text).strip()
        if not text or text == prev_text or (prev_text and text in prev_text):
            continue

        prev_text = text
        parsed_cues.append({
            "start_sec": start_sec,
            "end_sec": end_sec,
            "timestamp_str": format_timestamp(start_sec),
            "speaker": speaker,
            "text": text
        })

    return parsed_cues

def extract_keyframes(
    video_path: str,
    output_frames_dir: str,
    meeting_name: str,
    hash_threshold: int = 18,
    min_dwell_sec: float = 6.0
):
    """
    Samples video at 1 FPS, uses perceptual hashing to detect distinct slides,
    and calculates active time intervals [T_start, T_end].
    """
    os.makedirs(output_frames_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        raise ValueError(f"Unable to open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / fps

    print(f"🎬 Processing Video: {os.path.basename(video_path)}")
    print(f"⏱️  Duration: {format_timestamp(duration_sec)} ({duration_sec:.1f}s) at {fps:.1f} FPS")
    print(f"🔍 Sampling at 1 FPS with pHash threshold={hash_threshold}, min_dwell={min_dwell_sec}s...")

    raw_slides = []
    sec = 0.0
    last_hash = None
    last_saved_time = -999.0

    while sec < duration_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000)
        ret, frame = cap.read()
        if not ret:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        h = imagehash.phash(pil_img)

        if last_hash is None:
            raw_slides.append({"start_sec": sec, "hash": h, "image": pil_img})
            last_hash = h
            last_saved_time = sec
        else:
            diff = h - last_hash
            if diff >= hash_threshold and (sec - last_saved_time) >= min_dwell_sec:
                raw_slides.append({"start_sec": sec, "hash": h, "image": pil_img})
                last_hash = h
                last_saved_time = sec

        sec += 1.0

    cap.release()

    # Calculate intervals and save frames with timestamped filenames
    slides = []
    for i, slide in enumerate(raw_slides):
        start_sec = slide["start_sec"]
        end_sec = raw_slides[i + 1]["start_sec"] if i + 1 < len(raw_slides) else duration_sec
        
        start_str = format_file_timestamp(start_sec)
        end_str = format_file_timestamp(end_sec)
        filename = f"{meeting_name}_frame_{i+1:03d}_{start_str}_to_{end_str}.png"
        filepath = os.path.join(output_frames_dir, filename)

        slide["image"].save(filepath, "PNG", optimize=True)

        slides.append({
            "slide_index": i + 1,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start_time_str": format_timestamp(start_sec),
            "end_time_str": format_timestamp(end_sec),
            "filename": filename,
            "relative_path": os.path.join("frames", filename),
            "filepath": filepath,
            "duration_sec": round(end_sec - start_sec, 1)
        })

    print(f"✅ Extracted {len(slides)} distinct presentation slides/frames.")
    return slides, duration_sec

def compile_meeting_document(
    slides,
    cues,
    output_dir: str,
    meeting_title: str,
    meeting_name: str,
    video_duration_sec: float
):
    """
    Pairs dialogue cues with active slide intervals and outputs structured Markdown + metadata.json.
    """
    os.makedirs(output_dir, exist_ok=True)
    markdown_path = os.path.join(output_dir, "meeting_notes.md")
    metadata_path = os.path.join(output_dir, "metadata.json")

    # Group cues into slides
    sections = []
    for slide in slides:
        s_start = slide["start_sec"]
        s_end = slide["end_sec"]

        # Find all cues that started during this slide's active window
        slide_cues = [
            c for c in cues
            if s_start <= c["start_sec"] < s_end
        ]

        sections.append({
            "slide_index": slide["slide_index"],
            "start_time_str": slide["start_time_str"],
            "end_time_str": slide["end_time_str"],
            "duration_sec": slide["duration_sec"],
            "image_filename": slide["filename"],
            "image_relative_path": slide["relative_path"],
            "dialogue": slide_cues,
            "dialogue_text": " ".join([c["text"] for c in slide_cues])
        })

    # Generate Markdown content
    md_lines = [
        f"# {meeting_title}",
        "",
        f"- **Meeting ID / Name:** `{meeting_name}`",
        f"- **Total Duration:** {format_timestamp(video_duration_sec)}",
        f"- **Captured Slides / Sections:** {len(slides)}",
        f"- **Dialogue Cues Processed:** {len(cues)}",
        "",
        "---",
        ""
    ]

    for sec in sections:
        md_lines.append(f"## Slide {sec['slide_index']:02d} [{sec['start_time_str']} - {sec['end_time_str']}] (Duration: {sec['duration_sec']}s)")
        md_lines.append("")
        md_lines.append(f"![Slide {sec['slide_index']}]({sec['image_relative_path']})")
        md_lines.append("")

        if sec["dialogue"]:
            md_lines.append("### Spoken Discussion:")
            for d in sec["dialogue"]:
                speaker_tag = f"**{d['speaker']}**" if d['speaker'] else "**Speaker**"
                md_lines.append(f"- `[{d['timestamp_str']}]` {speaker_tag}: {d['text']}")
        else:
            md_lines.append("*(No spoken dialogue recorded during this slide interval)*")

        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    with open(markdown_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # Save metadata for Phase 2 RAG
    metadata = {
        "meeting_title": meeting_title,
        "meeting_name": meeting_name,
        "duration_sec": video_duration_sec,
        "duration_str": format_timestamp(video_duration_sec),
        "total_slides": len(slides),
        "total_cues": len(cues),
        "markdown_file": "meeting_notes.md",
        "sections": sections
    }

    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"📄 Generated Markdown Notes: {markdown_path}")
    print(f"📦 Generated Phase 2 Metadata: {metadata_path}")
    return markdown_path, metadata_path

def main():
    parser = argparse.ArgumentParser(description="Meeting Documentation Engine (Phase 1)")
    parser.add_argument("--video", required=True, help="Path to input meeting MP4 video")
    parser.add_argument("--vtt", required=True, help="Path to input WebVTT transcript file")
    parser.add_argument("--output-dir", default="./output", help="Directory to save generated notes and frames")
    parser.add_argument("--title", default=None, help="Human-readable meeting title")
    parser.add_argument("--threshold", type=int, default=12, help="pHash sensitivity threshold (default: 18)")
    parser.add_argument("--min-dwell", type=float, default=6.0, help="Minimum slide dwell time in seconds (default: 6.0)")

    args = parser.parse_args()

    video_path = os.path.abspath(args.video)
    vtt_path = os.path.abspath(args.vtt)
    meeting_name = os.path.splitext(os.path.basename(video_path))[0]
    meeting_title = args.title or meeting_name.replace("_", " ").title()

    meeting_output_dir = os.path.join(os.path.abspath(args.output_dir), meeting_name)
    frames_output_dir = os.path.join(meeting_output_dir, "frames")

    print("=" * 70)
    print(f"🚀 Starting Meeting Documentation Pipeline")
    print(f"📌 Meeting Title : {meeting_title}")
    print(f"📁 Output Target : {meeting_output_dir}")
    print("=" * 70)

    # Step 1: Parse VTT Transcript
    print("\n[Step 1/3] Parsing Transcript...")
    cues = parse_vtt(vtt_path)
    print(f"✅ Parsed {len(cues)} clean dialogue segments from transcript.")

    # Step 2: Extract Keyframes
    print("\n[Step 2/3] Extracting Video Slides & Calculating Intervals...")
    slides, duration_sec = extract_keyframes(
        video_path=video_path,
        output_frames_dir=frames_output_dir,
        meeting_name=meeting_name,
        hash_threshold=args.threshold,
        min_dwell_sec=args.min_dwell
    )

    # Step 3: Compile Markdown & Metadata
    print("\n[Step 3/3] Compiling Interleaved Document...")
    md_path, meta_path = compile_meeting_document(
        slides=slides,
        cues=cues,
        output_dir=meeting_output_dir,
        meeting_title=meeting_title,
        meeting_name=meeting_name,
        video_duration_sec=duration_sec
    )

    print("\n" + "=" * 70)
    print("🎉 Phase 1 Processing Complete!")
    print(f"📝 Document: {md_path}")
    print(f"🖼️  Frames  : {frames_output_dir} ({len(slides)} slides)")
    print("=" * 70)

if __name__ == "__main__":
    main()
