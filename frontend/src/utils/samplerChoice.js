/**
 * The sampler choice for a MiniMax H3 Image graph with a picture input (the
 * Run popup and the character sheet step). LCM is the saved graph's own; Euler
 * is the run's copy with a KSamplerSelect in its place.
 */
export const SAMPLER_CHOICES = [
  { id: "lcm", label: "LCM" },
  { id: "euler", label: "Euler" },
];

export const SAMPLER_HINT =
  "LCM keeps the person closest; Euler keeps a little more skin detail.";
