"""A synthetic 4-minute lecture in YouTube auto-caption style, with notes and where each belongs."""

SECONDS_PER_CAPTION = 4

CAPTIONS = [
    "hey everyone welcome back today we're going to talk about",
    "photosynthesis which is honestly one of the most important",
    "processes on the planet so photosynthesis is how plants make",
    "their own food using energy from sunlight they take in",
    "carbon dioxide and water and they turn it into sugar",
    "and they release oxygen which is pretty great for us",
    "so where does this actually happen it happens inside the",
    "chloroplasts which are these little organelles inside the leaf cells",
    "a single leaf cell can have dozens of chloroplasts in it",
    "and inside the chloroplast there's a pigment called chlorophyll",
    "chlorophyll is the green pigment that captures the light",
    "it absorbs mostly red and blue light and it reflects",
    "green light which is why leaves look green to our eyes",
    "okay so the whole process is split into two stages",
    "the first stage is called the light reactions and the second",
    "stage is called the calvin cycle let's start with the first one",
    "the light reactions take place in the thylakoid membrane",
    "the thylakoids are these stacked discs inside the chloroplast",
    "when light hits the chlorophyll it excites electrons and",
    "to replace those electrons the plant splits water molecules",
    "when water is split oxygen gets released as a byproduct",
    "that's where the oxygen we breathe comes from",
    "the energy from those electrons is used to make two things",
    "atp and nadph which are energy carrying molecules",
    "think of them like little rechargeable batteries for the cell",
    "so at the end of the light reactions we have atp we have nadph",
    "and we've released oxygen now let's move on to stage two",
    "the calvin cycle happens in the stroma which is the fluid",
    "that surrounds the thylakoids inside the chloroplast",
    "the calvin cycle doesn't need light directly which is why",
    "people sometimes call it the light independent reactions",
    "the first step is carbon fixation carbon dioxide from the air",
    "gets attached to a five carbon molecule by an enzyme called rubisco",
    "rubisco is probably the most abundant enzyme on earth",
    "then the cell uses the atp and the nadph from the light reactions",
    "to build a three carbon sugar called g3p",
    "two g3p molecules can be combined to make one glucose",
    "and the rest gets recycled to keep the cycle going",
    "now how does carbon dioxide get into the leaf in the first place",
    "on the underside of the leaf there are tiny pores called stomata",
    "the stomata open to let carbon dioxide in but when they're open",
    "water vapor escapes out so the plant is always balancing",
    "getting enough carbon dioxide against losing too much water",
    "on really hot dry days the plant closes its stomata to save water",
    "but then carbon dioxide runs low and oxygen builds up",
    "and rubisco starts grabbing oxygen by mistake",
    "this wasteful process is called photorespiration",
    "and it can cut the plant's sugar production quite a bit",
    "some plants have evolved clever ways around this problem",
    "c4 plants like corn and sugarcane separate the steps in space",
    "they concentrate carbon dioxide in special bundle sheath cells",
    "cam plants like cactus and pineapple separate the steps in time",
    "they open their stomata at night and store the carbon dioxide",
    "then they run the calvin cycle during the day with stomata closed",
    "so to recap light reactions in the thylakoid make atp and nadph",
    "the calvin cycle in the stroma uses them to build sugar",
    "and stomata control the gas exchange for the whole thing",
    "next time we'll look at cellular respiration which is basically",
    "the reverse process where cells break sugar down for energy",
    "thanks for watching and i'll see you in the next one",
]

# (what the student wrote, caption index where the lecture says it, or None if nowhere)
NOTES = [
    ("Photosynthesis = how plants make food from sunlight", 2),
    ("happens in chloroplasts (inside leaf cells)", 6),
    ("chlorophyll absorbs red + blue, reflects green", 10),
    ("2 stages: light rxns + Calvin cycle", 13),
    ("light rxns in thylakoid membrane", 16),
    ("water split -> O2 released", 19),
    ("makes ATP + NADPH (energy carriers)", 22),
    ("Calvin cycle in stroma, no light needed", 27),
    ("CO2 fixed by rubisco", 31),
    ("ATP + NADPH used to build G3P sugar", 34),
    ("stomata = pores, CO2 in / water out", 39),
    ("hot days -> stomata close -> photorespiration", 43),
    ("C4 (corn) + CAM (cactus) = adaptations", 49),
    ("TEST FRIDAY!! ch. 8", None),
    ("* chlorophyll = green pigment", 10),  # added at the bottom of the page, said early on
]


def pasted_transcript() -> str:
    """The captions as text copied from YouTube's 'Show transcript' panel."""
    out = []
    for index, text in enumerate(CAPTIONS):
        seconds = index * SECONDS_PER_CAPTION
        out.append("%d:%02d" % (seconds // 60, seconds % 60))
        out.append(text)
    return "\n".join(out)
