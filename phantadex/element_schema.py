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
        "current_time": {
            "selectors": [
                "span.current-time-display",
                "span[aria-label='Video Progress']",
                "div[role='slider']",
            ],
            "description": "Current playback timestamp",
        },
        "duration": {
            "selectors": ["span.duration-display", "div.video-player-progress-bar span:last-child"],
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
