from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.api import routes
from backend.app import app
from backend.config import settings
from backend.prompts import PromptComposer
from backend.storage import BuilderJobStore, IdeaStore, PromptTemplateStore, SessionStore, StateStore
from backend.workflows import WorkflowModeResolver
from backend.models.core import ApprovalMode, ApprovalStatus, Proposal, WorkflowExecutionMode, WorkflowState, WorkflowStatus


class OperatorCoherenceFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state_dir = root / "data"
        self.job_dir = root / "bridge" / "builder_jobs"
        self.session_dir = root / "data" / "sessions"
        self.idea_dir = root / "data" / "ideas"
        self.prompt_dir = root / "data" / "prompts"

        self._original = {
            "state_dir": settings.state_dir,
            "builder_job_dir": settings.builder_job_dir,
            "session_dir": settings.session_dir,
            "idea_thread_dir": settings.idea_thread_dir,
            "prompt_template_dir": settings.prompt_template_dir,
            "local_network_only": settings.local_network_only,
            "store": routes.store,
            "job_store": routes.job_store,
            "session_store": routes.session_store,
            "idea_store": routes.idea_store,
            "prompt_store": routes.prompt_store,
            "prompt_composer": routes.prompt_composer,
        }

        settings.state_dir = self.state_dir
        settings.builder_job_dir = self.job_dir
        settings.session_dir = self.session_dir
        settings.idea_thread_dir = self.idea_dir
        settings.prompt_template_dir = self.prompt_dir
        settings.local_network_only = False

        routes.store = StateStore(self.state_dir)
        routes.job_store = BuilderJobStore(self.job_dir)
        routes.session_store = SessionStore(self.session_dir)
        routes.idea_store = IdeaStore(self.idea_dir)
        routes.prompt_store = PromptTemplateStore(self.prompt_dir)
        routes.prompt_composer = PromptComposer(routes.prompt_store)

        self.client = TestClient(app)
        self.workflow_id = "wfcoherence01"
        self.proposal_id = "propcoh1"
        self._seed_workflow()

    def tearDown(self) -> None:
        settings.state_dir = self._original["state_dir"]
        settings.builder_job_dir = self._original["builder_job_dir"]
        settings.session_dir = self._original["session_dir"]
        settings.idea_thread_dir = self._original["idea_thread_dir"]
        settings.prompt_template_dir = self._original["prompt_template_dir"]
        settings.local_network_only = self._original["local_network_only"]
        routes.store = self._original["store"]
        routes.job_store = self._original["job_store"]
        routes.session_store = self._original["session_store"]
        routes.idea_store = self._original["idea_store"]
        routes.prompt_store = self._original["prompt_store"]
        routes.prompt_composer = self._original["prompt_composer"]
        self.tmp.cleanup()

    def _seed_workflow(self) -> None:
        resolver = WorkflowModeResolver()
        policy = resolver.resolve(WorkflowExecutionMode.NORMAL)
        providers = resolver.provider_roles_for(policy)
        state = WorkflowState(
            id=self.workflow_id,
            status=WorkflowStatus.APPROVED,
            approval_mode=ApprovalMode.AUTO_WITH_LIMITS,
            workflow_mode=WorkflowExecutionMode.NORMAL,
            resolved_policy=policy,
            providers=providers,
            proposals=[
                Proposal(
                    id=self.proposal_id,
                    batch_index=1,
                    prompt="Implement the approved orchestrator-safe change.",
                    files_affected=["orchestrator/ui/app.js"],
                    token_count=120,
                    approval=ApprovalStatus.APPROVED,
                )
            ],
            context={
                "title": "Coherence integration workflow",
                "scope": "orchestrator only",
                "goal": "Lock end-to-end coherence path",
            },
            current_stage="approved",
        )
        routes.store.save(state)

    def test_dispatch_assignment_preview_delivery_followup_result_stays_non_completed(self) -> None:
        dispatch = self.client.post(
            f"/api/workflows/{self.workflow_id}/builder-jobs",
            json={"proposal_id": self.proposal_id, "category": "build"},
        )
        self.assertEqual(dispatch.status_code, 200, dispatch.text)
        job = dispatch.json()["job"]
        job_id = job["id"]
        self.assertEqual(job["workflow_id"], self.workflow_id)
        self.assertEqual(job["proposal_id"], self.proposal_id)

        create_a = self.client.post("/api/sessions", json={"session_id": "claude-a", "role": "claude"})
        create_b = self.client.post("/api/sessions", json={"session_id": "claude-b", "role": "claude"})
        self.assertEqual(create_a.status_code, 200, create_a.text)
        self.assertEqual(create_b.status_code, 200, create_b.text)

        assign_a = self.client.post(
            "/api/sessions/claude-a/assign",
            json={"job_id": job_id, "next_expected_action": "Deliver prompt contract to session."},
        )
        self.assertEqual(assign_a.status_code, 200, assign_a.text)
        self.assertEqual(assign_a.json()["session"]["assigned_job_id"], job_id)
        self.assertEqual(assign_a.json()["session"]["status"], "assigned")

        assign_b = self.client.post(
            "/api/sessions/claude-b/assign",
            json={"job_id": job_id, "next_expected_action": "Deliver prompt contract to session."},
        )
        self.assertEqual(assign_b.status_code, 400)
        self.assertIn("already actively assigned", assign_b.text)

        preview = self.client.get("/api/sessions/claude-a/prompt-preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_payload = preview.json()
        self.assertTrue(preview_payload["preview_only"])
        self.assertEqual(preview_payload["session"]["session_id"], "claude-a")
        self.assertEqual(preview_payload["job"]["job_id"], job_id)
        self.assertEqual(preview_payload["workflow"]["workflow_id"], self.workflow_id)
        self.assertTrue(preview_payload["prompt"]["prompt_text"])
        self.assertGreater(preview_payload["prompt"]["token_estimate"], 0)

        legacy_result = self.client.post(
            "/api/sessions/claude-a/result",
            json={
                "outcome": "needs_followup",
                "last_result_summary": "Legacy pre-delivery result should be rejected.",
                "notes": "Should fail before manual delivery.",
                "output_ref": "note://legacy-rejected",
                "artifacts": ["note://legacy-rejected"],
                "next_expected_action": "Do not allow pre-delivery result entry.",
                "metadata": {"review_tag": "integration-test"},
            },
        )
        self.assertEqual(legacy_result.status_code, 400)
        self.assertIn("waiting_for_result or running", legacy_result.text)

        delivered = self.client.post(
            "/api/sessions/claude-a/mark-delivered",
            json={"operator": "test-operator", "note": "Delivered manually in integration test."},
        )
        self.assertEqual(delivered.status_code, 200, delivered.text)
        self.assertEqual(delivered.json()["session"]["status"], "waiting_for_result")
        self.assertEqual(delivered.json()["delivery"]["job_id"], job_id)

        record = self.client.post(
            "/api/sessions/claude-a/result",
            json={
                "outcome": "needs_followup",
                "last_result_summary": "Delivered work needs a follow-up pass before completion.",
                "notes": "Needs follow-up to finish edge-case handling.",
                "output_ref": "note://followup-needed",
                "artifacts": ["note://followup-needed"],
                "next_expected_action": "Create follow-up job.",
                "metadata": {"review_tag": "integration-test"},
            },
        )
        self.assertEqual(record.status_code, 200, record.text)
        record_payload = record.json()
        self.assertEqual(record_payload["session"]["last_result_summary"], "Delivered work needs a follow-up pass before completion.")
        self.assertEqual(record_payload["session"]["status"], "waiting_for_result")
        self.assertEqual(record_payload["linked"]["job_id"], job_id)
        self.assertIsNotNone(record_payload["linked"]["next_step"])
        self.assertEqual(record_payload["linked"]["next_step"]["ingestion"]["outcome"], "needs_followup")

        workflow = self.client.get(f"/api/workflows/{self.workflow_id}")
        self.assertEqual(workflow.status_code, 200, workflow.text)
        workflow_payload = workflow.json()
        self.assertEqual(workflow_payload["status"], "approved")
        self.assertNotEqual(workflow_payload["status"], "completed")
        self.assertEqual(workflow_payload["current_stage"], "result_needs_followup")

        queue = self.client.get("/api/builder-jobs/queue")
        self.assertEqual(queue.status_code, 200, queue.text)
        queue_item = next(item for item in queue.json()["items"] if item["job_id"] == job_id)
        self.assertEqual(queue_item["workflow"]["workflow_id"], self.workflow_id)
        self.assertEqual(queue_item["workflow"]["proposal_id"], self.proposal_id)
        self.assertEqual(queue_item["metadata"]["last_outcome"], "needs_followup")


if __name__ == "__main__":
    unittest.main()
