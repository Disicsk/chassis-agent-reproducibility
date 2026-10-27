# Results and limits

All figures below can be regenerated from the released run records. The primary unit for paired comparisons is the task, averaging its two sessions. The reported scenarios and models do not form a random sample of all engineering tasks or all LLMs.

| Study | Fixed | General agent | Skill agent |
|---|---:|---:|---:|
| DeepSeek v4, original 12 tasks | 18/24 | 23/24 | 23/24 |
| DeepSeek v5, six new T2 tasks | 6/12 | 12/12 | 11/12 |
| DeepSeek, combined ten T2 tasks | 8/20 | 19/20 | 18/20 |
| GLM replication, original 12 tasks | 24/24 | 24/24 | 24/24 |

The combined T2 agent-minus-fixed difference is 55 percentage points, with the reported task-resampled 95% interval [25, 85] and two-sided exact sign-test p = 0.0312. This combination includes the four tasks that exposed the issue and six subsequently added tasks. It is an exploratory pooled analysis. The six added tasks alone give p = 0.25; they should not be described as an independently significant confirmation.

In these DeepSeek failures, the specification could remain physically feasible after a requirement change while the final submission retained the previous input. Candidate feasibility and correct adoption of current requirements are separate checks.

With GLM, all groups passed the tested tasks. Average requests were 6.62 for F, 10.38 for B and 9.88 for S. Using unrounded means, F used 36.1% fewer requests than B and 32.9% fewer than S. This is a model-request comparison, not an established reduction in money, engineer work time or physical computation.

The v3 direct/fixed/agent comparison gave 21/24, 23/24 and 24/24 valid designs, with 5.17, 5.12 and 9.17 mean requests. The one-run difference between fixed and agent does not establish a general superiority claim. Tool-grounded final submissions can be traced to engineering checks under their current inputs; direct-text numerical explanations were not all independently validated sentence by sentence.

## Implications

Traceable engineering execution is the shared practical foundation. Where the tested model missed requirement updates, agent scheduling offered additional feedback/checking opportunities; where updates were reliable, a fixed schedule achieved the observed validity with fewer requests. The experiments compare concrete implementations and do not causally isolate every prompt, interface or scheduling component.

## Remaining gaps

- Two service labels, with unknown immutable backend versions; future behaviour may drift.
- Two crawler configurations and structured synthetic tasks.
- Scripted benchmark reviews rather than a systematic human-in-the-loop user study.
- No broad vehicle-level or dynamic validation and no measured human-time savings.
- v5 S groups collected on a different day; they were not contemporaneously randomized with F/B.
- Scores reflect modeled constraints, input consistency and defined preferences; they do not certify unrestricted natural-language explanation quality or physical safety.

These constraints are not resolved merely by adding more repetitions or model names.
