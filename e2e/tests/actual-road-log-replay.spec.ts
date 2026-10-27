import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const selected = process.env.AXIOM_ACTUAL_ROAD_LOG_ACCEPTANCE;

test('original road GT source is selected, inspected and replayed through project-scoped product UI', async ({ page }) => {
  test.skip(!selected, 'Opt-in requires a retained original road-log product acceptance, explicit project grant and local Engineer token; no authored-source fallback.');
  const acceptance = JSON.parse(fs.readFileSync(selected!, 'utf8'));
  expect(acceptance.status).toBe('actual_original_road_log_reference_queue_accepted');
  expect(acceptance.commercial_use).toBe(false);
  const token = fs.readFileSync(path.join(path.dirname(selected!), 'engineer-token.txt'), 'utf8').trim();
  const proposal = JSON.parse(fs.readFileSync(path.join(path.dirname(selected!), 'converted/import-proposal.json'), 'utf8'));
  await page.addInitScript((value) => sessionStorage.setItem('axiom.apiToken', value), token);
  await page.goto('/#replay-specs');
  await expect(page.getByLabel('Replay project')).toHaveValue(acceptance.project_id);
  await page.getByLabel('Source type').selectOption('recording');
  await page.getByLabel('Granted recording source').selectOption(acceptance.recording_id);
  await expect(page.getByText('Annotation-derived geometry is research ground truth, not sensor detections.', {exact:false})).toBeVisible();
  const sourceDownloadEvent = page.waitForEvent('download');
  await page.getByRole('button', {name:'Download original MCAP', exact:true}).click();
  const sourceDownload = await sourceDownloadEvent;
  expect(sourceDownload.suggestedFilename()).toBe(`${acceptance.recording_id}.mcap`);
  const downloadedSource = fs.readFileSync((await sourceDownload.path())!);
  expect(createHash('sha256').update(downloadedSource).digest('hex')).toBe(acceptance.recording_sha256);
  await expect(page.getByText('Original MCAP downloaded; SHA-256 and byte size verified. Source-use restrictions remain unchanged.', {exact:true})).toBeVisible();
  await page.getByLabel('Recorded ego channel').selectOption(String(proposal.mcap.ego_channel_id));
  await page.getByLabel('Recorded object channel').selectOption(String(proposal.mcap.object_channel_id));
  await page.getByRole('button', {name:'Read exact ego samples', exact:true}).click();
  await page.getByRole('button', {name:'Select loaded samples', exact:true}).click();
  await expect(page.getByText('Selected 3/512 exact observations', {exact:false})).toBeVisible();
  await page.getByLabel('Declared world frame').fill(proposal.mcap.world_frame);
  await page.getByLabel('Declared clock domain').fill('nuscenes-original-capture-unix-ns');
  await page.getByLabel('Recorded sequence name').fill('Browser original road GT · isolated research');
  await page.getByRole('button', {name:'Inspect selected inputs', exact:true}).click();
  await expect(page.getByText('Selected observations are structurally executable in the supported reference scope.', {exact:true})).toBeVisible();
  await expect(page.getByText('Unknown object velocities remain null; corresponding TTC observations stay unevaluated.', {exact:true})).toBeVisible();
  const freezeResponse = page.waitForResponse((response) => response.url().endsWith('/api/v1/scene-sequences/freeze') && response.request().method()==='POST');
  await page.getByRole('button', {name:'Freeze inspected sequence', exact:true}).click();
  const frozen = await (await freezeResponse).json();
  expect(frozen.snapshot.schema_version).toBe('scene-sequence/2');
  expect(frozen.snapshot.source_use_scope.commercial_use).toBe(false);
  expect(frozen.snapshot.frames.map((frame:any) => frame.objects.objects.length)).toEqual([78,86,94]);
  await expect(page.getByLabel('Frozen sequence')).toHaveValue(frozen.id);
  await page.getByLabel('Replay name').fill('Browser original GT reference feedback');
  await page.getByLabel('Replay mode').selectOption('planning_closed_loop');
  const replayResponse = page.waitForResponse((response) => response.url().endsWith('/api/v1/replay-specs') && response.request().method()==='POST');
  await page.getByRole('button', {name:'Save replay specification', exact:true}).click();
  const replay = await (await replayResponse).json();
  await page.getByRole('button', {name:'Check retained inputs', exact:true}).click();
  await expect(page.getByRole('button', {name:'Open planning experiment', exact:true})).toBeEnabled();
  await page.getByRole('button', {name:'Open planning experiment', exact:true}).click();
  await expect(page.getByLabel('Execution type')).toHaveValue('planning_replay');
  await page.getByRole('button', {name:'Preview workload', exact:true}).click();
  await expect(page.getByText('Preview SHA-256:', {exact:false})).toBeVisible();
  await page.getByRole('button', {name:'Save frozen experiment', exact:true}).click();
  await expect(page.getByRole('button', {name:'Submit execution', exact:true})).toBeEnabled();
  const submitResponse = page.waitForResponse((response) => response.url().includes('/api/v1/experiments/') && response.url().endsWith('/executions') && response.request().method()==='POST');
  await page.getByRole('button', {name:'Submit execution', exact:true}).click();
  const submitted = await (await submitResponse).json();
  let result:any;
  await expect.poll(async () => { result=await (await page.request.get(`/api/v1/experiments/executions/${submitted.id}`, {headers:{Authorization:`Bearer ${token}`}})).json();return result.status; }).toBe('completed');
  await expect(page.getByRole('img', {name:'Replay trajectory and object observations', exact:true})).toBeVisible();
  expect(result.summary.source_use_scope.commercial_use).toBe(false);
  expect(result.summary.observer_counts.ttc).toEqual({evaluated:0,unevaluated:3});
  expect(result.summary.verification_verdict).toBe('diagnostic_not_safety_validation');
  expect(result.summary.max_trajectory_error_m).toBeGreaterThan(0);
  const download=page.waitForEvent('download');
  await page.getByRole('button', {name:'Download sealed receipt', exact:true}).click();
  expect((await download).suggestedFilename()).toBe(`${result.id}.json`);
  await expect(page.getByText('Native evidence downloaded and SHA-256 verified.')).toBeVisible();
  await page.screenshot({path:test.info().outputPath('actual-road-log-reference-feedback.png'),fullPage:true});
  fs.writeFileSync(test.info().outputPath('actual-road-log-browser-acceptance.json'),JSON.stringify({
    schema_version:'actual-road-log-browser-acceptance/1',status:'passed',actual_original_source_no_fixture_fallback:true,
    project_id:acceptance.project_id,recording_id:acceptance.recording_id,recording_sha256:acceptance.recording_sha256,
    source_grant_id:acceptance.source_grant_id,source_grant_manifest_hash:acceptance.source_grant_manifest_hash,
    original_source_download_sha256:acceptance.recording_sha256,original_source_download_size_bytes:downloadedSource.byteLength,
    sequence_id:frozen.id,sequence_sha256:frozen.sha256,replay_spec_id:replay.id,job_id:result.id,manifest_hash:result.manifest_hash,
    commercial_use:false,source_denominator:acceptance.source_denominator,verification_verdict:result.summary.verification_verdict,
    observer_counts:result.summary.observer_counts,maximum_reference_trajectory_difference_m:result.summary.max_trajectory_error_m,
    workflow:['project_granted_original_source_picker','download_original_mcap_sha_verified','inspect_original_pose_and_clocks','select_exact_ego_ordinals','preview_missing_velocity_and_rights','freeze_inspected_source_hash','author_replay_intent','preview_and_save_experiment','submit_common_queue','inspect_reference_feedback','download_sealed_sha_verified_receipt']
  },null,2));
});
