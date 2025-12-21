# Project TODOs

## FFmpeg Optimizations
- [ ] **Prevent Upscaling in Phase 2**: In `clip_two_step_generation`, update the FFmpeg scale filter for the 480p generation to prevent upscaling if the source video has a height lower than 480px.
    - **Current**: `'scale=-2:480'`
    - **Proposed**: `'scale=-2:min(480\,ih)'`
    - **Reason**: Currently, if Step 1 downloads a 360p video (because 720p isn't available), Step 2 will upscale it to 480p, resulting in a blurry video. Using `min(480, ih)` ensures we only downscale or keep the original size.
