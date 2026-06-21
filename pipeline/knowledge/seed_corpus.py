"""Curated, research-backed knowledge base of what makes ads succeed.

This is the seed corpus loaded into the Redis vector index. Each entry is a
distilled, reusable principle drawn from advertising-effectiveness research
(Binet & Field / IPA, Ehrenberg-Bass), applied neuro/attention research, and
the three reference films shipped in /downloads (Coca-Cola "Masterpiece",
Coca-Cola "For Everyone", Apple "iPad Pro — Float"). The live-research module
appends fresh, product-specific findings to the same index at request time.

Everything here is oriented toward realistic, short-form native audio-video —
the format Cerebra optimizes for — so retrieval surfaces tactics that translate
into a Seedance 2.0 generation prompt.
"""

from __future__ import annotations

from ..schema import KnowledgeDoc

SEED_DOCS: list[KnowledgeDoc] = [
    # ---------------------------- attention / neuro ----------------------------
    KnowledgeDoc(
        id="hook-first-second",
        title="Win the first second or lose the view",
        category="principle",
        content=(
            "On short-form feeds the scroll decision is made in under a second. "
            "Open on the single most arresting frame — motion, a face mid-expression, "
            "an unexpected object, or a pattern interrupt — never on a logo or a slow "
            "establishing shot. Front-load the payoff; do not build to it. Visual "
            "novelty in frame one is the strongest predictor of watch-through."
        ),
    ),
    KnowledgeDoc(
        id="faces-and-eyes",
        title="Faces and direct gaze capture attention",
        category="principle",
        content=(
            "Human faces, and especially direct eye contact, are processed "
            "pre-attentively and reliably draw and hold the viewer's gaze. A real, "
            "emotionally legible human face early in the clip raises attention and "
            "the felt authenticity of the message. Emotion on a face transfers to "
            "the brand."
        ),
    ),
    KnowledgeDoc(
        id="emotion-beats-information",
        title="Emotion outperforms rational information",
        category="principle",
        content=(
            "Emotionally driven campaigns outperform rational, feature-led ones on "
            "long-term brand effects (Binet & Field, IPA dataset). Feeling is what "
            "gets encoded and recalled; specs are forgotten. Lead with a feeling — "
            "joy, awe, relief, belonging — and let the product ride on it. One clear "
            "emotion beats three muddled ones."
        ),
    ),
    KnowledgeDoc(
        id="peak-end-rule",
        title="Engineer a peak and a strong ending",
        category="principle",
        content=(
            "Memory of an experience is dominated by its emotional peak and its end "
            "(peak-end rule). Design one unmistakable peak moment and a deliberate, "
            "satisfying final beat. A flat ending wastes the whole clip. In short-form, "
            "the last frame should reward the watch and carry the brand."
        ),
    ),
    KnowledgeDoc(
        id="distinctive-assets",
        title="Brand with distinctive assets, not just logos",
        category="principle",
        content=(
            "Ehrenberg-Bass: brands grow by being easy to notice and recall via "
            "distinctive assets — a signature color, shape, sound, character, or "
            "motion. Bake the brand's distinctive cue into an action or object the "
            "viewer remembers, and surface it at the emotional peak, not just as an "
            "end-card logo. Brand early and often enough to be attributed."
        ),
    ),
    KnowledgeDoc(
        id="sound-on-design",
        title="Design for sound-on, survive sound-off",
        category="principle",
        content=(
            "Short-form is increasingly watched with sound on; native audio, a "
            "spoken hook, and a beat-matched cut multiply engagement. But the core "
            "story must still read with the sound off — carry meaning in the visual "
            "and in on-screen captions so the ad works either way."
        ),
    ),
    KnowledgeDoc(
        id="show-dont-tell",
        title="Show the transformation, don't narrate it",
        category="principle",
        content=(
            "Demonstration beats description. Show the before/after, the first bite, "
            "the moment the product does its job — let the viewer infer the claim from "
            "what they see. Sensory, concrete action converts; adjectives do not. If a "
            "line could be replaced by a shot, use the shot."
        ),
    ),
    KnowledgeDoc(
        id="single-minded-message",
        title="One idea per ad",
        category="principle",
        content=(
            "Effective ads are single-minded: one product, one benefit, one feeling, "
            "one memorable device. Every extra message dilutes recall and attribution. "
            "In 15 seconds there is room for exactly one thing the viewer should "
            "remember — decide what it is before writing a single beat."
        ),
    ),
    # ------------------------------ structure ----------------------------------
    KnowledgeDoc(
        id="shortform-beat-structure",
        title="Short-form 5-beat structure: HOOK → 3 cuts → OUTRO",
        category="structure",
        content=(
            "A reliable 15s skeleton: HOOK (0–3s, pattern interrupt + the promise), "
            "then three fast JUMP CUTS (3–12s) that escalate — setup, reveal, twist — "
            "then an OUTRO (12–15s) that lands the feeling and the brand. Hard cuts "
            "every ~3s keep retention high. Each beat earns the next; no dead frames."
        ),
    ),
    KnowledgeDoc(
        id="vertical-framing",
        title="Vertical 9:16 framing and safe zones",
        category="structure",
        content=(
            "Short-form is 9:16 vertical. Compose for a tall frame: subject centered "
            "or upper-third, action in the middle band, and keep the lower ~15% and the "
            "top ~10% clear of critical detail so captions and platform UI don't cover "
            "it. Tight, single-subject shots read far better than wide scenes on a phone."
        ),
    ),
    KnowledgeDoc(
        id="pacing-words-per-second",
        title="Dialogue pacing for short-form",
        category="structure",
        content=(
            "For spoken short-form, target ~5.5–6 words per second so a 15s ad lands "
            "around 85–90 words total, split across the beats. Each beat gets one tight "
            "spoken line that advances the arc. Faster feels native and energetic; "
            "slower drags and viewers bounce."
        ),
    ),
    # --------------------------- reference films -------------------------------
    KnowledgeDoc(
        id="ref-coke-masterpiece",
        title="Reference: Coca-Cola 'Masterpiece'",
        category="reference_ad",
        industry="beverage",
        content=(
            "A bottle of Coke passes between figures stepping out of famous artworks "
            "to reach a tired student. Technique: an imaginative, visually-stunning "
            "chain of hand-offs (a passing-the-bottle motif) turns a simple act of "
            "sharing into spectacle. The product is the connective thread through every "
            "scene; the distinctive red and the contour bottle anchor brand attribution. "
            "Lesson for realistic short-form: build one striking visual device that the "
            "product literally moves through, and keep the brand's signature color and "
            "silhouette present in every beat."
        ),
    ),
    KnowledgeDoc(
        id="ref-coke-for-everyone",
        title="Reference: Coca-Cola 'For Everyone'",
        category="reference_ad",
        industry="beverage",
        content=(
            "Real, diverse people enjoying Coke in everyday moments, celebrating "
            "individuality under one shared brand. Technique: authenticity and human "
            "warmth over polish — relatable settings, genuine expressions, inclusive "
            "casting. The feeling is belonging. Lesson for realistic short-form: cast "
            "real-feeling people in true-to-life settings, let unguarded emotion carry "
            "the spot, and tie the product to a universal human moment."
        ),
    ),
    KnowledgeDoc(
        id="ref-ipad-float",
        title="Reference: Apple 'iPad Pro — Float'",
        category="reference_ad",
        industry="tech",
        content=(
            "Colorful objects and the iPad drift weightlessly against a clean "
            "background to a calm track, communicating thinness and lightness without "
            "a word of spec. Technique: a single elegant visual metaphor (floating = "
            "impossibly light), minimalist staging, premium negative space, and "
            "product-as-hero. Lesson for realistic short-form: pick ONE physical "
            "property to dramatize, stage it cleanly with lots of negative space, and "
            "let the product be the most beautiful object on screen."
        ),
    ),
    # -------------------------- Seedance 2.0 craft ------------------------------
    KnowledgeDoc(
        id="pika-realistic-look",
        title="Seedance craft: direct photoreal materials, optics, and physics",
        category="pika_craft",
        content=(
            "Seedance 2.0 responds to director-level physical specificity. Describe "
            "lens feel, depth of field, motivated light, exposure, skin and material "
            "texture, contact, weight, momentum, liquid behavior, reflections, and "
            "true-to-life color. Use one intentional camera path. Realism comes from "
            "concrete optical and physical direction, not from repeating 'realistic'."
        ),
    ),
    KnowledgeDoc(
        id="pika-prompt-anatomy",
        title="Seedance craft: chronological audio-video prompt anatomy",
        category="pika_craft",
        content=(
            "Write one chronological production brief. Anchor the unchanged subject, "
            "product, wardrobe, packaging, and setting, then structure each timed beat "
            "as action → camera → performance → light → synchronized dialogue, foley, "
            "ambience, and music. Use concrete filmable nouns and verbs. One primary "
            "action per beat improves instruction following and motion stability."
        ),
    ),
    KnowledgeDoc(
        id="pika-trust-references",
        title="Seedance craft: preserve continuity and simplify generated screens",
        category="pika_craft",
        content=(
            "State continuity explicitly across the whole timeline: same face, hands, "
            "wardrobe, product count, product geometry, packaging, signature colors, "
            "screen direction, environment, and lighting logic. Avoid depending on "
            "dense generated UI or tiny typography; show one simple outcome and let "
            "performance, product action, distinctive assets, and audio carry meaning."
        ),
    ),
    KnowledgeDoc(
        id="pika-negative-prompt",
        title="Seedance craft: express guardrails as positive requirements",
        category="pika_craft",
        content=(
            "The Seedance generation API does not accept a separate negative prompt. "
            "Translate risks into positive instructions in the main prompt: stable "
            "anatomy and identity, consistent hands and face, one unchanged product, "
            "clean packaging geometry, smooth natural motion, coherent exposure and "
            "lighting, intentional cuts, photoreal textures, and a clean frame."
        ),
    ),
    KnowledgeDoc(
        id="seedance-biomechanical-motion",
        title="Seedance craft: choreograph physical action through biomechanics",
        category="pika_craft",
        content=(
            "Complex physical action looks believable when the prompt describes its "
            "causal phases: anticipation, planted support, weight transfer, joint "
            "rotation, contact, follow-through, and balanced recovery. Keep the center "
            "of mass supported and synchronize impact sound to contact. A soccer kick "
            "needs the support foot planted beside the ball, arm counterbalance, hip "
            "rotation, knee extension, instep contact with slight compression, natural "
            "leg follow-through, and a smooth ball trajectory with believable speed, "
            "spin, gravity, bounce, and friction. Require continuous motion and natural "
            "shutter blur rather than sliding feet, snapping limbs, skipped contact, "
            "teleportation, or instantaneous changes in direction."
        ),
    ),
    KnowledgeDoc(
        id="ugc-authenticity",
        title="Realistic UGC feels unpolished on purpose",
        category="pika_craft",
        content=(
            "Creator-style realism reads as authentic when it is slightly raw: handheld "
            "POV or selfie framing, natural indoor light, real-room backgrounds, "
            "conversational delivery, tiny imperfections. Over-polished, studio-perfect "
            "footage reads as an ad and gets skipped. Match the texture of the feed."
        ),
    ),
    # --------------------------- industry-specific -----------------------------
    KnowledgeDoc(
        id="ind-beverage",
        title="Beverage ads: sensory peak and refreshment",
        category="principle",
        industry="beverage",
        content=(
            "Beverages convert on the sensory peak: the pour, condensation, the fizz, "
            "the first sip and satisfied exhale. Shoot it macro and in motion, with cold "
            "cues (frost, droplets) and the brand color saturated. Tie the drink to a "
            "moment of relief, connection, or reward."
        ),
    ),
    KnowledgeDoc(
        id="ind-tech",
        title="Tech/hardware ads: one property, product as hero",
        category="principle",
        industry="tech",
        content=(
            "Dramatize a single physical or experiential property (thin, fast, quiet, "
            "powerful) with one clean visual metaphor and generous negative space. The "
            "device is the hero object; the first-use moment is the conversion beat. "
            "Avoid spec lists; show the thing doing the thing."
        ),
    ),
    KnowledgeDoc(
        id="ind-beauty",
        title="Beauty ads: matched-light before/after",
        category="principle",
        industry="beauty",
        content=(
            "Beauty converts on visible transformation under identical lighting and "
            "angle for the before and after, plus the tactile application ritual "
            "(glide, glow, droplet). Time-stamped social proof ('day 14') signals real "
            "use over paid promo."
        ),
    ),
    KnowledgeDoc(
        id="ind-food",
        title="Food ads: hunger response over description",
        category="principle",
        industry="food",
        content=(
            "Food converts on appetite appeal: steam, sizzle, cheese pull, the cut, the "
            "first bite. Shoot the sensory peak in macro and let it play; never describe "
            "flavor in words when you can show texture and motion."
        ),
    ),
    KnowledgeDoc(
        id="ind-saas",
        title="SaaS/app ads: show live UI doing the thing in under 5s",
        category="principle",
        industry="saas",
        content=(
            "Software converts when the viewer sees the real UI accomplish the job in "
            "under five seconds. Frame the screen as the product, keep one clear "
            "interaction, and bookend with human social proof. State what it replaces in "
            "the user's workflow, not a feature list."
        ),
    ),
    KnowledgeDoc(
        id="ind-fitness",
        title="Fitness ads: earned payoff after relatable resistance",
        category="principle",
        industry="fitness",
        content=(
            "Fitness converts on relatable struggle followed by earned result. Name the "
            "friction ('almost skipped today'), show the work (sweat, breath, the rep), "
            "then the payoff. Showing the product alone is not enough — show the effort "
            "it enables."
        ),
    ),
]
