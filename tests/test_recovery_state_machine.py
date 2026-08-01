"""Attempt lineage and bounded recovery policy for Ticket #5."""

import unittest

from hoya_market_agents.recovery_state_machine import RecoveryStateMachine


class RecoveryStateMachineTest(unittest.TestCase):
    def setUp(self):
        self.machine = RecoveryStateMachine(
            ("news",),
            {"news": "claude-opus"},
            {"news": "gpt-5.6-sol"},
        )

    def test_recovery_is_same_model_once_then_cross_model_once(self):
        primary = self.machine.start_all()[0]

        retry = self.machine.recover(primary.attempt_id, "provider_error")
        replacement = self.machine.recover(retry.attempt_id, "timeout")
        exhausted = self.machine.recover(replacement.attempt_id, "process_error")

        self.assertEqual("news-a1", primary.attempt_id)
        self.assertEqual("claude-opus", retry.model)
        self.assertEqual("same_model_retry", retry.kind)
        self.assertEqual("gpt-5.6-sol", replacement.model)
        self.assertEqual("cross_model_replacement", replacement.kind)
        self.assertEqual(primary.attempt_id, replacement.original_attempt_id)
        self.assertEqual(retry.attempt_id, replacement.parent_attempt_id)
        self.assertIsNone(exhausted)

    def test_replacement_receives_public_checkpoint_without_impersonating_original(self):
        state = self.machine.seats["news"]
        primary = state.primary()
        state.save_checkpoint({"claims": ["public checkpoint"]})
        retry = state.recover(primary.attempt_id, "timeout")
        replacement = state.recover(retry.attempt_id, "timeout")

        self.assertEqual({"claims": ["public checkpoint"]}, replacement.checkpoint)
        self.assertNotEqual(primary.attempt_id, replacement.attempt_id)
        self.assertEqual(primary.attempt_id, replacement.original_attempt_id)

    def test_adopted_seat_never_spawns_another_recovery_attempt(self):
        primary = self.machine.start_all()[0]
        self.machine.seats["news"].mark_adopted(primary.attempt_id)

        self.assertIsNone(self.machine.recover(primary.attempt_id, "timeout"))

    def test_cross_model_replacement_cannot_silently_use_same_model(self):
        machine = RecoveryStateMachine(
            ("news",), {"news": "opus"}, {"news": "opus"}
        )
        primary = machine.start_all()[0]
        retry = machine.recover(primary.attempt_id, "provider_error")

        with self.assertRaisesRegex(ValueError, "不同模型"):
            machine.recover(retry.attempt_id, "timeout")


if __name__ == "__main__":
    unittest.main()
