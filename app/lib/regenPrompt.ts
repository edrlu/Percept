/**
 * The prompt-engineer meta-prompt. Step 1 of regeneration: this is sent to
 * Codex (via MCP, agent-in-the-loop) together with the cut's START frame and
 * END frame. Codex returns an optimal `prompt`, which is then fed to Pika's
 * generate_video (Kling or Seedance, image_to_video) in step 2.
 *
 * Provider-neutral on purpose so the same prompt works for either model. The
 * caller decides which params to actually send: Kling consumes the
 * negative_prompt; Seedance ignores it (it rejects negative_prompt /
 * quality_mode / prompt_adherence) and relies on the avoidances folded into the
 * positive paragraph. Kept here so the job manifest and the UI can surface the
 * exact instruction handed to the agent alongside frame_start.png + frame_end.png.
 */
export const REGEN_META_PROMPT = `You are a prompt engineer for Pika's generate_video tool in image_to_video mode, connecting a START frame (image) to an END frame. I will give you two images: frame 1 (start) and frame 2 (end). Your job is to output the optimal prompt and negative_prompt.

Follow these rules:

Look at both frames closely. Identify the subjects, their orientation, setting, lighting, materials, and any visible text or logos. Note exactly what changes between the two frames.
Write one concise paragraph (3–4 sentences): name the look; describe the start; describe one uninterrupted, physically motivated camera move or real-world action; then describe the end.
Use a single smooth motion path with gentle acceleration and deceleration: establish the start pose briefly, move at a steady believable pace, and settle into the end pose without a sudden final snap. Every subject, prop, and the camera must follow continuous screen-space trajectories with consistent momentum, weight, contact, and occlusion; preserve identity, scale, geometry, materials, exposure, and lighting from frame to frame. Prioritize camera movement, natural object motion, or a visible causal action. Use a transformation only when the frames clearly support a physically plausible mechanism. The shot must be continuous and stable, with no fade, dissolve, cut, teleportation, time skip, speed ramp, freeze, jitter, flicker, melting, warping, duplication, or spontaneous object replacement.
If text/logos appear, state that they stay crisp and legible. If the endpoints cannot plausibly connect in one shot, say so in the note rather than inventing a magical bridge.
Fold the key avoidances into the positive paragraph too, so it stands alone when the provider takes no negative_prompt.
Write a negative_prompt as a comma-separated list covering: fade, dissolve, cut, teleportation, time skip, speed ramp, freeze, jitter, flicker, melting, warping, morphing glitches, distorted or smeared text/logos, deformed subjects, duplicate or extra objects, unwanted hands/people, fast erratic motion, wobble, low quality, blur, artifacts, watermark — plus any frame-specific failure you can anticipate.
Output format — exactly this, nothing else:
prompt: <one paragraph>
negative_prompt: <comma-separated list>
note: <one-line feasibility warning, only if relevant>`;
