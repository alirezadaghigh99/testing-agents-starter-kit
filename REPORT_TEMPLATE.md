# Report: <team name>

Copy this file to `REPORT.md` and replace every placeholder in angle brackets. Keep it under about
1500 words, tables included. Every number must come from the evaluator's output on the development
(training) problems.

## 1. Team

<names and student numbers>

## 2. Final configuration

Strategy in `student/final.yaml`: <separate or combined>

<describe in a few sentences what your final agent does, step by step>

## 3. Prompts and tools

| Part | Mode | What it does | Why it is there |
|---|---|---|---|
| <prompt or tool name> | <blackbox, whitebox, combined or shared> | <describe> | <describe> |

## 4. Results on the development problems

Fill in from `python -m testagent.evaluate --mode final` for your final configuration and from the
same command on a run of the unchanged starter kit.

| Metric | Starter baseline | Your final configuration |
|---|---|---|
| Bug detection rate | <number> | <number> |
| Hard-mutant score | <number> | <number> |
| Path coverage | <number> | <number> |
| Oracle precision | <number> | <number> |
| Branch coverage | <number> | <number> |
| Tokens per problem | <number> | <number> |
| Cost for all problems (USD) | <number> | <number> |

## 5. What you tried

At least three things you tried, including ones that did not help. For each: what you changed, the
numbers before and after, and what you learned.

| Change | Bug detection | Hard-mutant score | Path coverage | Kept? |
|---|---|---|---|---|
| <describe> | <number> | <number> | <number> | <yes or no> |

## 6. Design questions

1. Which part of your design improved bug detection the most, and how do you know?
2. How does your agent decide on expected values in black-box mode, and how did you reduce wrong ones?
3. What does the white-box side of your agent add over the black-box side? With the separate strategy, use
   the evaluator's "black-box tests alone" numbers. With the combined strategy, compare against a run of
   your black-box prompt and tools on their own.
4. How does your agent decide which tests to keep within the call budget?
5. Describe two problems where your agent still does badly, and why, using their trajectories.
