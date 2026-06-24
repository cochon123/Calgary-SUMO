#!/usr/bin/env python3
"""
PPO smoke test for Calgary corridor signal optimization.

Bandit formulation: agent picks 1 of 7 plans, episode runs 360 env-steps,
reward = mean_speed per step.

Usage:
    SUMO_HOME=/usr/share/sumo python3 train_ppo.py [--timesteps 10000]
"""
import argparse
import os
import sys
import time

# SUMO must be on path before importing the env
SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from stable_baselines3.common.vec_env import DummyVecEnv

from calgary_env import CalgaryCorridorEnv, PLAN_NAMES


class RewardLogger(BaseCallback):
    """Log episode rewards and save learning curve data."""

    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_plans = []
        self.current_episode_reward = 0.0
        self.current_episode_length = 0
        self.start_time = None

    def _on_training_start(self):
        self.start_time = time.monotonic()

    def _on_step(self):
        # Accumulate reward
        reward = self.locals["rewards"][0]
        self.current_episode_reward += reward
        self.current_episode_length += 1

        # Check if episode ended
        dones = self.locals.get("dones", [False])
        if dones[0]:
            self.episode_rewards.append(self.current_episode_reward)
            self.episode_lengths.append(self.current_episode_length)
            # Track which plan was used (from info)
            infos = self.locals.get("infos", [{}])
            plan = infos[0].get("plan", "?") if infos else "?"
            self.episode_plans.append(plan)
            self.current_episode_reward = 0.0
            self.current_episode_length = 0

            # Print every few episodes
            n = len(self.episode_rewards)
            if n % 5 == 0 or n <= 5:
                elapsed = time.monotonic() - self.start_time
                avg_r = sum(self.episode_rewards[-5:]) / min(5, len(self.episode_rewards))
                recent_plans = self.episode_plans[-5:]
                plan_counts = {}
                for p in recent_plans:
                    plan_counts[p] = plan_counts.get(p, 0) + 1
                top_plan = max(plan_counts, key=plan_counts.get)
                print(
                    f"  Episode {n:>4} | "
                    f"reward={self.episode_rewards[-1]:>6.2f} | "
                    f"avg5={avg_r:>6.2f} | "
                    f"last_plan={plan:<20} | "
                    f"t={elapsed:>6.1f}s",
                    flush=True,
                )
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=10_000)
    parser.add_argument("--n-steps", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--save", type=str, default="output/ppo_smoke_test.zip")
    args = parser.parse_args()

    print("=" * 60)
    print("  PPO SMOKE TEST — Calgary Corridor Signal Optimization")
    print("=" * 60)
    print(f"  Action space:    Discrete(7) — {PLAN_NAMES}")
    print(f"  Observation:     Box(66,) — 50 edges + 16 TLS phases")
    print(f"  Reward:          mean_speed (dense, per env-step)")
    print(f"  Timesteps:       {args.timesteps}")
    print(f"  n_steps:         {args.n_steps}")
    print(f"  Learning rate:   {args.learning_rate}")
    print()

    # Create environment
    print("Creating environment...", flush=True)
    env = CalgaryCorridorEnv()

    # Quick sanity: reset and one step
    print("Sanity check: reset + step...", end=" ", flush=True)
    obs, info = env.reset()
    obs2, reward, term, trunc, info2 = env.step(0)
    print(f"OK | obs_range=[{obs.min():.3f}, {obs.max():.3f}] | reward={reward:.4f}")
    env.close()

    # Wrap in DummyVecEnv (required by SB3)
    vec_env = DummyVecEnv([lambda: CalgaryCorridorEnv()])

    # Create PPO agent
    print("\nCreating PPO agent...", flush=True)
    model = PPO(
        "MlpPolicy",
        vec_env,
        n_steps=args.n_steps,
        learning_rate=args.learning_rate,
        verbose=0,
        seed=42,
    )

    # Train
    print(f"\nTraining for {args.timesteps} timesteps...\n", flush=True)
    logger = RewardLogger(verbose=0)
    t0 = time.monotonic()
    model.learn(total_timesteps=args.timesteps, callback=logger)
    t_train = time.monotonic() - t0

    # Save model
    save_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.save)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    model.save(save_path)

    # Results
    print()
    print("=" * 60)
    print("  TRAINING COMPLETE")
    print("=" * 60)
    print(f"  Wall time:         {t_train:.1f}s ({t_train/60:.1f} min)")
    print(f"  Timesteps:         {args.timesteps}")
    print(f"  Throughput:        {args.timesteps/t_train:.1f} steps/s")
    print(f"  Episodes:          {len(logger.episode_rewards)}")
    if logger.episode_rewards:
        rewards = logger.episode_rewards
        print(f"  Reward — first 5:  {sum(rewards[:5])/min(5,len(rewards)):.2f}")
        print(f"  Reward — last 5:   {sum(rewards[-5:])/min(5,len(rewards)):.2f}")
        print(f"  Reward — best:     {max(rewards):.2f}")
        print(f"  Reward — worst:    {min(rewards):.2f}")

    # Plan distribution
    if logger.episode_plans:
        from collections import Counter
        plan_counts = Counter(logger.episode_plans)
        print(f"\n  Plan selection frequency:")
        for plan, count in plan_counts.most_common():
            pct = count / len(logger.episode_plans) * 100
            bar = "█" * int(pct / 5)
            print(f"    {plan:<20} {count:>4} ({pct:>5.1f}%) {bar}")

    # Save learning curve data
    import json
    curve_path = os.path.join(os.path.dirname(save_path), "ppo_learning_curve.json")
    with open(curve_path, "w") as f:
        json.dump({
            "episode_rewards": [float(r) for r in logger.episode_rewards],
            "episode_lengths": [int(l) for l in logger.episode_lengths],
            "episode_plans": logger.episode_plans,
            "config": {
                "timesteps": args.timesteps,
                "n_steps": args.n_steps,
                "learning_rate": args.learning_rate,
            },
            "wall_time_s": t_train,
        }, f, indent=2)
    print(f"\n  Learning curve:    {curve_path}")
    print(f"  Model saved:       {save_path}")

    vec_env.close()


if __name__ == "__main__":
    main()
