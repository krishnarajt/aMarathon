# Building Amarathon, a daily walking competition with friends

The problem with most fitness apps is not that they lack numbers.

They have steps, distance, calories, rings, streaks, charts, goals, badges, and sometimes so many graphs that going for a walk starts to feel like maintaining a production dashboard.

What I wanted was much simpler:

**What if walking felt like a small game with friends?**

That became Amarathon.

## The basic idea

Every day, Amarathon gives a group a target. Everyone walks toward it, the app tracks the progress, and somebody wins the day.

That is the whole loop. You can add friends, create challenges, accept invitations, watch the leaderboard change, and look back at the history. There is enough structure to make the walk matter, but not so much that the app becomes the activity.

The nice thing about a daily competition is that it resets. A bad Tuesday does not ruin an eight-week plan. Wednesday is another chance.

## Building the actual product

Amarathon is split into two repositories.

The backend is FastAPI with a proper database and migrations. It owns accounts, friendships, challenge invitations, daily targets, activity records, and results. Local username and password login still works, which makes development and self-hosting straightforward.

The frontend is React, Vite, Tailwind, Framer Motion, and Recharts. I wanted it to feel energetic without turning every interaction into a celebration. The main screens are about today: the target, your progress, where your friends are, and who is currently ahead. History and charts are there when you want the longer view.

For local development I also built a seed script. It creates four demo players, active challenges, invitations, friend requests, and enough historical data to see every important state. This sounds like a small detail, but it made developing a social app much easier. Empty social apps are impossible to judge.

## Authentication without coupling everything together

Amarathon can also sit behind my API Get Away gateway and use Authentik.

The gateway authenticates a user and injects identity headers. Amarathon does not blindly trust those headers, because the backend can also have a direct public address. A shared gateway secret proves that the request actually passed through the trusted gateway before the identity is accepted.

That gave me both options: uncomplicated local accounts and a clean single sign-on path for my hosted apps.

## What I like about it

Amarathon is deliberately not a complete health platform. It does not try to diagnose anything or optimize your entire life.

It takes one healthy activity, adds a target, adds friends, and makes the result visible.

Sometimes motivation is not another reminder. Sometimes it is seeing that your friend is 600 steps ahead and deciding that absolutely cannot stand.

