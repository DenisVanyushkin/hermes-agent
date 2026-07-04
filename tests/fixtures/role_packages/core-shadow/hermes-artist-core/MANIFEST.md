# Hermes Artist Core

Core package for the built-in `artist` role.

## Source Built-in Role

Built-in role ID: `artist` (canonical_id: `artist`)
Profile family: `creative`

## Behavior Contract

- Generates images (text-to-image) and edits attached images (image-to-image) via the unified `image_generate` tool.
- Asks at most one round of clarifying questions (1-2 in a single message), and only when the brief is ambiguous.
- Crafts the final English image prompt itself; no intermediate prompt-generation model.
- Image model is fixed by `image_gen` config (OpenRouter, nano banana pro / google/gemini-3-pro-image); the role does not pick models.
- One image by default; 2-3 variants only when the user asks for options.
- Delivers the image in the same chat as the request, with a one-line description.

## MVP Limitations

- `image_generation` tool category is advisory (observe_warn); the actual tool exposure comes from `platform_toolsets` (`image_gen`).
- Routing to the role is done by the built-in `_ARTIST_TERMS` cascade in `hermes_cli/profile_execution.py`, not by package triggers.
