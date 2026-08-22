"""Selector candidates for every element the tool knows how to find.

Held in a leaf module with no imports of its own: both the runtime resolver in
:mod:`phantadex.schema` and the discovery package read it, and neither should
have to import the other to do so.
"""

# Targeted Elements based on Plan.md and Coursera UI patterns
ELEMENTS_SCHEMA = {
    "course_metadata": {
        "course_name": {
            "selectors": [
                "a[title*='Home Page']",
                "a.cds-150",
                "a.cds-341.css-yrq2q5",
                "nav[aria-label='Breadcrumbs'] li:first-child",
            ],
            "description": "The main course title link",
        },
        "module_name": {
            "selectors": [
                "button.cds-AccordionHeader-button[aria-expanded='true'] span",
                "nav[aria-label='Breadcrumbs'] li:last-child",
                "span.css-6ecy9b",
            ],
            "description": "Current active module header",
        },
    },
    "video_controls": {
        "play_button": {
            "selectors": [
                "button[aria-label='Play']",
                "button[data-testid='play-button']",
                "svg[data-testid='PlayArrowSvg']",
            ],
            "description": "Video play/pause trigger",
        },
        "pause_button": {
            "selectors": [
                "button[aria-label='Pause']",
                "button[data-testid='pause-button']",
                "svg[data-testid='PauseSvg']",
            ],
            "description": "Control that stops a playing video",
        },
        "progress_bar": {
            # The seekable timeline is a span, not a div, and the slider role
            # belongs to the drag handle inside it -- a 10px square. Both of the
            # selectors that matched the handle are gone from here: a click at a
            # fraction of a 10px width lands somewhere in the handle rather than
            # at a timestamp, so matching it is worse than matching nothing. The
            # rail is kept last because it spans the full timeline like the bar.
            "selectors": [
                "[data-testid='video-progress-bar']",
                ".video-player-progress-bar",
                ".video-player-progress-bar-rail",
            ],
            "description": "Timeline a click can seek through",
        },
        "current_time": {
            # The tagless form is the fallback: it survives the element changing
            # from a span to a div, which is the change this class is likely to
            # see. A slider is never a timestamp, so no slider selector belongs here.
            "selectors": [
                "span.current-time-display",
                ".current-time-display",
            ],
            "description": "Current playback timestamp",
        },
        "duration": {
            # The former last-child fallback resolved to the progress bar's drag
            # handle, whose text is empty -- it read as a duration of nothing.
            "selectors": ["span.duration-display", ".duration-display"],
            "description": "Total video length",
        },
        "mute_button": {
            "selectors": [
                "button[aria-label='Mute']",
                "button[aria-label='Unmute']",
                "svg[data-testid='VolumeUpSvg']",
            ],
            "description": "Audio toggle",
        },
    },
    "transcript": {
        "transcript_container": {
            "selectors": [
                ".rc-Transcript",
                ".rc-TranscriptHighlighter",
                "button:has-text('Transcript')",
            ],
            "description": "Interactive transcript area",
        },
        "downloads_tab": {
            "selectors": [
                "[data-testid='item-tool-panel-button-files']",
                "button[aria-label='Files']",
                "button:has-text('Downloads')",
                "[data-testid='downloads-tab']",
                "a:has-text('Downloads')",
            ],
            "description": "Files/Downloads control for lesson assets",
        },
        "transcript_download_link": {
            "selectors": [
                "a[download='transcript.txt']",
                "li:has-text('Transcript') a[download$='.txt']",
                "a:has-text('Transcript')",
                "li:has-text('Transcript') a",
                "a:has-text('.txt')",
            ],
            "description": "Link to download the transcript file",
        },
    },
    "content": {
        "reading_body": {
            "selectors": [
                "div.rc-ReadingItemDisplay",
                "div.rc-CML",
                "div[role='presentation']",
                "[data-testid='cml-viewer']",
            ],
            "description": "Main reading text container",
        }
    },
    # Controls of an AI roleplay practice item, served under the /coach/ segment.
    # The item is only marked complete once a started dialogue has been ended
    # and the ending confirmed, so each step of that sequence is addressed here.
    "dialogue": {
        "start": {
            "selectors": [
                "button:has-text('Start Dialogue')",
                "button[aria-label='Start Dialogue']",
            ],
            "description": "Control that opens a roleplay dialogue",
        },
        "end": {
            "selectors": [
                "button[aria-label='End Dialogue']",
                "button:has-text('End Dialogue')",
            ],
            "description": "Control that closes a running roleplay dialogue",
        },
        "confirm_end": {
            "selectors": [
                "[role='dialog'] button:has-text('Yes, end the Dialogue')",
                "button:has-text('Yes, end the Dialogue')",
            ],
            "description": "Confirmation in the 'end this session?' prompt",
        },
        "finished": {
            "selectors": [
                "button:has-text('Try again')",
                "button[aria-label='Try again']",
            ],
            "description": "Retry control shown once a dialogue has ended",
        },
    },
    "navigation": {
        "next_item": {
            "selectors": [
                "button[aria-label='Go to next item']",
                "button[data-testid='next-item']",
                "button:has-text('Go to next item')",
            ],
            "description": "Button to advance to next lesson",
        },
        "previous_item": {
            "selectors": [
                "button[aria-label='Go to previous item']",
                "button[data-testid='previous-item']",
                "button:has-text('Go to Previous Item')",
            ],
            "description": "Control returning to the previous lesson",
        },
        "mark_complete": {
            "selectors": [
                "button[data-testid='mark-complete']",
                "button:has-text('Mark as completed')",
            ],
            "description": "Reading completion button",
        },
        "completed_stamp": {
            "selectors": ["a[aria-label*='Completed']", "h3:has-text('Completed')"],
            "description": "Visual confirmation of completion",
        },
    },
    "sidebar": {
        "success_icon": {
            "selectors": ["[data-testid='learn-item-success-icon']"],
            "description": "Green checkmark in sidebar",
        }
    },
}
