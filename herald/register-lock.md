# Register Lock: Emergent Comedy Through Analytical Fidelity

The Herald's comedy must be involuntary. The writing agents never try to be funny. They report committee behavior with clinical precision and the comedy emerges from the gap between the register and the absurdity of the subject matter.

## The mechanism

A locked analytical register encountering mismatched subject matter produces humor without intent. The comedy is structural - it exists in the juxtaposition itself, regardless of whether the writer knows it's being funny or not.

This was observed in practice: a strategy tool analyzing a crude joke produced "dominance signal wrapped in a dick joke" - six words that land as comedy because a clinical voice delivered them with full seriousness. The tool wasn't performing deadpan. It was reporting findings. The involuntary quality is what makes it impossible to look away from.

The same mechanism applies to committee politics. A parliamentary journalist describing a scheduling decision with analytical precision makes the political game visible in a way that editorializing cannot. The absurdity speaks for itself when the register refuses to acknowledge it.

## Implication for writer prompts

**Do not instruct writing agents to be sardonic, witty, or humorous.** Any instruction that makes the model aware it should be funny produces performed comedy - the AI equivalent of a comedian winking at the audience.

Instead:

- Lock the register to serious political journalism
- Instruct the model to describe committee behavior with the same precision a parliamentary correspondent uses for a legislature
- Never acknowledge absurdity in the prompt - the model must believe it is doing serious analytical work at all times
- The model must process whatever it encounters through its analytical framework without flinching or moderating

## What this means for the specialist fleet

The diversity mechanism (3-writer.md) routes drafts through different specialists. Register lock does not mean all drafts sound the same - it means no draft is trying to be funny. A "terse news" specialist and an "editorial commentary" specialist will both produce comedy when reporting on committee absurdity, but the comedy will have different texture because the analytical frame differs. The key constraint is: no specialist has "humor" or "sardonic" or "wit" in its system prompt.

## The operator's role

The comedy is conjured by the operator's choice of subject matter, not by the tool's register. The Herald's collection and intelligence layers decide what is newsworthy. The writer's locked register makes the newsworthy material land with comedic force. The editor selects drafts where the mechanism fired hardest - where the clinical description of a committee action is funniest precisely because it isn't trying.

## Anti-patterns

- "Write with dry wit" - performed
- "Be occasionally sardonic" - tells the model to signal awareness of absurdity
- "Note the irony" - breaks the fourth wall
- "With a touch of humor" - model will add jokes instead of reporting facts that are themselves funny
- Any temperature jitter intended to produce "funnier" outputs - diversity of angle is fine, chasing laughter is not

## The test

If you removed the subject matter and replaced it with a mundane topic, the output should read as competent journalism. If it reads as someone trying to be funny about a mundane topic, the register leaked. The comedy must live entirely in the collision between register and subject, never in the register alone.
