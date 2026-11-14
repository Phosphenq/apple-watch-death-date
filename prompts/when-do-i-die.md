# When do I die? (no install)

Paste this into Claude Code together with your Health export (Health app on the iPhone > profile picture in the top right > Export All Health Data):

```
I attached my Apple Health export. This is my own data. I want to see what my Apple Watch has kept on me and, from my own numbers, when I'm likely to die.

Done means: a privacy map of the export, my full-history trends, a death date model with the math shown, and the one habit that moves it. Short report at the end.

- Read export.xml with a streaming parser, it can be gigabytes. Count every record type, sources and devices, how often my heart rate was sampled, how many single heartbeats sit inside HRV sessions (InstantaneousBeatsPerMinute), nights of sleep, workout routes in workout-routes/*.gpx and what share of them start from the same spot. Never print the exact coordinates or my full birth date.
- Build month-by-month curves for vo2max, resting heart rate, HRV and sleep over the whole history, not the last week.
- Compare my recent vo2max with the FRIEND registry median for my age and sex (Kaminsky et al., Mayo Clin Proc 2015): percentile and fitness age. Compare my resting heart rate with CDC NHANES 1999-2008 for my age and sex.
- Death model: the US SSA period life table for my sex, from my exact age (the birth date is in the <Me> element of the export). Multiply my yearly risk by 0.87 for every MET of vo2max above the median (Kodama et al., JAMA 2009) and by 1.16 for every 10 bpm of resting heart rate above the mean (Jensen et al., Heart 2013). Build my survival curve year by year: coin-flip year (50%), death window (25% to 75%), and the same coin-flip year for an average person my age.
- The one habit: rerun the model with vo2max +10% and tell me how many years it buys.
- Read-only. Nothing gets uploaded anywhere. It's a model, say so once, then be brutally honest: if my heart is older than me, say it.

End with: Your numbers, Your window, The one habit.
```

Want it done for you in one command instead? Install the skill from this repo and type `/death-date`.
