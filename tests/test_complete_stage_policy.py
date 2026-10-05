"""Request-policy tests; no linguistic approval or suffix validator introduced."""
import asyncio,copy,json
from pathlib import Path
import pytest
from pipeline import annotation_review_guidance as guidance,annotation_adjudication as adju,annotation_research as research
from pipeline.korean_chunk_reviews import review_request,review_chunk,verify_review
from tests.test_korean_chunk_reviews import Runner
ROOT=next(parent for parent in Path(__file__).resolve().parents if (parent/'pipeline').is_dir())
HERE=ROOT/'runs/maintenance/20261003/smokes/normalized-chunk13-adjudication-20261005'

def test_historical_adju1to6_research2to7_exact_policies():
 # Baseline manifest is generated before any isolated imports.
 p=Path(__file__).with_name('historical-policies.json')
 if not p.exists():p=ROOT/'tests/fixtures/complete_stage_historical_policies.json'
 baseline=json.loads(p.read_text())
 assert {str(v):adju.digest(adju._versioned_instructions(v)) for v in range(1,7)}==baseline['adjudication']
 assert {str(v):[research._digest(x) for x in research._policies(v)] for v in range(2,8)}==baseline['research']
 assert adju.digest(guidance.FORM_STAGE_EVIDENCE_GUIDANCE)==baseline['shared_guidance']
 assert guidance.ENDING_REPLACEMENT_GUIDANCE in adju._versioned_instructions(7)
 assert all(guidance.ENDING_REPLACEMENT_GUIDANCE in x for x in research._policies(8))

@pytest.mark.parametrize('version',[True,2.0,3,None])
def test_unknown_instruction_versions_fail_closed(version):
 with pytest.raises(ValueError):guidance.form_stage_guidance(version)

def test_actual13_and122_shared_representation_contract_preserves_raw_and_targets():
 inputs={'candidate':{'segments':[{} for _ in range(123)]},'source_text':'못했고 시켰는지','context':{'chunk_review_context':{'chapter_text':'못했고 시켰는지','source_start':0}},'current_review':{'approved':False,'issues':[{'candidate_paths':['/segments/13/form_steps/0/form'],'supporting_paths':[],'explanation':'Preserved current finding'}]}}
 inputs['candidate']['segments'][13]={'text':'못했고','form_steps':[{'form':'못했다'},{'form':'못했고'}]}
 inputs['candidate']['segments'][122]={'text':'시켰는지','form_steps':[{'form':'시켰다'},{'form':'시켰는지'}]}
 before=copy.deepcopy(inputs)
 for index in (13,122):
  segment=inputs['candidate']['segments'][index]
  request,instructions,_=review_request(inputs['candidate'],inputs['source_text'],inputs['context']['chunk_review_context'],'Review.',guidance_version=2)
  assert request['annotation']['segments'][index]==segment
  assert request['complete_stage_instruction_policy_version']==2
  assert 'not a literal-substring list' in instructions and 'prefinal stem' in instructions
 assert inputs==before
 assert inputs['candidate']['segments'][13]['form_steps'][0]['form']=='못했다'
 assert inputs['candidate']['segments'][122]['form_steps'][0]['form']=='시켰다'
 # Preserve genuine actionablefinding rather than changing its linguisticstatus.
 assert inputs['current_review']==before['current_review']


def test_fresh_korean_review_receipt_marker_replays_and_tamper_rejects(tmp_path):
 candidate={'segments':[{'text':'아이'}]};context={'chapter_text':'아이','source_start':0};runner=Runner(tmp_path)
 review,receipt=asyncio.run(review_chunk(runner,tmp_path,annotation=candidate,text='아이',context=context,policy='Review.'))
 assert receipt['complete_stage_instruction_policy_version']==2
 verify_review(tmp_path,receipt,annotation=candidate,text='아이',chapter_text='아이',source_start=0)
 bad={**receipt,'complete_stage_instruction_policy_version':1}
 with pytest.raises(ValueError,match='instruction version'):verify_review(tmp_path,bad,annotation=candidate,text='아이',chapter_text='아이',source_start=0)


def test_actual_old_current11_receipt_replay_after_new_instruction_defaults():
 if not (HERE/'required-inputs.json').exists():pytest.skip('Optional retained genuine worker receipt absent')
 inputs=json.loads((HERE/'required-inputs.json').read_text());initial=json.loads((HERE/'adjudication-result.json').read_text());terminal=json.loads((HERE/'research-continuation-v2-result.json').read_text())
 run=ROOT/'runs/maintenance/20261003/smokes/reusable-damyeo-chunk13-acceptance-20261005/run-output'
 assert adju.verify_adjudication_evidence(run,initial,**inputs)['status']=='uncertain'
 assert adju.verify_adjudication_evidence(run,terminal,**inputs)['status']=='uncertain'


def test_unmarked_historical_sidecar_tamper_still_hits_exact_worker_inputs(tmp_path):
 from pipeline.korean_agent_harness import digest,payload,save
 from pipeline import korean_contracts as contracts
 candidate={'segments':[{'text':'아이'}]};context={'chapter_text':'아이','source_start':0};runner=Runner(tmp_path)
 inputs,instructions,identity=review_request(candidate,'아이',context,'Review.')
 assert 'complete_stage_instruction_policy_version' not in inputs
 job='annotation-local-review-'+identity
 review=asyncio.run(runner.call(job,'Review.\n'+instructions+payload(**inputs),contracts.schema_path('chunk-review-targeted'),'low',tool_profile='offline',workspace_context={'chunk_review_input':inputs}))
 save(tmp_path/'agents'/job/'review-input.json',inputs)
 receipt={'job':job,'input_digest':digest(inputs),'review_digest':digest(review),'form_review_guidance_digest':digest(guidance.FORM_STAGE_EVIDENCE_GUIDANCE),'issue_targets_version':1}
 verify_review(tmp_path,receipt,annotation=candidate,text='아이',chapter_text='아이',source_start=0)
 altered=copy.deepcopy(inputs);altered['annotation']['segments'][0]['meaning']='unreviewed';save(tmp_path/'agents'/job/'review-input.json',altered)
 with pytest.raises(ValueError,match='differ from the worker evidence'):
  verify_review(tmp_path,{**receipt,'input_digest':digest(altered)},annotation=altered['annotation'],text='아이',chapter_text='아이',source_start=0)


def test_japanese_exact_surface_gate_unchanged_and_cn_has_no_invented_chain():
 from pipeline.japanese_agent_harness import japanese_form_step_issues
 row={'surface':'乗せられて','surface_kana':'のせられて','type':'word','lemma':'乗せる','lemma_kana':'のせる','conjugation_form':'passive te-form','form_steps':[{'form':'乗せられる','reading':'のせられる','label':'passive','meaning_en':'be put'},{'form':'乗せられて','reading':'のせられて','label':'te-form','meaning_en':'be put, and'}]}
 assert japanese_form_step_issues(row)==[]
 broken=copy.deepcopy(row);broken['form_steps'][-1]['form']='乗せられる'
 assert any('final form step' in x for x in japanese_form_step_issues(broken))
 # A mechanicallywellformed wrongtense or barestem still requires independent
 # semanticreview; no suffixrule/validatorwaiver/forcedapproval is introduced.
 for form,meaning in [('乗せられ','be put'),('乗せられた','will be put')]:
  diagnostic=copy.deepcopy(row);diagnostic['form_steps'][0].update(form=form,meaning_en=meaning)
  assert 'prefinal stem' in guidance.form_stage_guidance(2)
  assert 'wrong tense' in guidance.form_stage_guidance(2)
  assert diagnostic['form_steps'][0]['form']==form
 assert 'Chinese segment/overlay representations do not acquire a form-step requirement' in guidance.form_stage_guidance(2)


def test_new_explicit_marker_requires_exact_new_guidance_digest(tmp_path):
 from pipeline.korean_agent_harness import digest
 candidate={'segments':[{'text':'아이','form_steps':[]}]};context={'chapter_text':'아이','source_start':0}
 review,receipt=asyncio.run(review_chunk(Runner(tmp_path),tmp_path,annotation=candidate,text='아이',context=context,policy='Review.'))
 forged={**receipt,'form_review_guidance_digest':digest(guidance.FORM_STAGE_EVIDENCE_GUIDANCE)}
 with pytest.raises(ValueError,match='version2 complete-stage guidance digest'):
  verify_review(tmp_path,forged,annotation=candidate,text='아이',chapter_text='아이',source_start=0)


def test_ja_old_unmarked_cache_context_remains_exact_and_new_marker_selects_new_policy(tmp_path,monkeypatch):
 from pipeline.japanese_agent_harness import verify_japanese_chunk_review
 source,item=_unmarked_ja_cache_setup(tmp_path,monkeypatch)
 assert verify_japanese_chunk_review(item,source,tmp_path)
 fresh=copy.deepcopy(item);replay=fresh['attempts'][-1]['adjudication_replay']
 replay['context']['complete_stage_instruction_policy_version']=2
 # The verifier must NOT infer freshguidance from a new marker alone.
 assert not verify_japanese_chunk_review(fresh,source,tmp_path)
 replay['known_reference_input']['review-policy']['content']['form_stage_guidance']=guidance.form_stage_guidance(2)
 assert verify_japanese_chunk_review(fresh,source,tmp_path)
 replay['context']['complete_stage_instruction_policy_version']=2.0
 assert not verify_japanese_chunk_review(fresh,source,tmp_path)


# Setup-only mocked final verdict; this fixture tests immutable-context selection, not linguistic approval.
def _unmarked_ja_cache_setup(tmp_path, monkeypatch):
    from pipeline.annotation_adjudication import digest as evidence_digest
    from pipeline.annotation_publication import bind_review_job
    from pipeline.agent_harness import CodexRunner
    from pipeline import annotation_adjudication

    from pipeline.japanese_agent_harness import japanese_semantic_repair_grammar_knowledge, JAPANESE_ANNOTATION_CHUNK_POLICY
    from pipeline.annotation_review_guidance import FORM_STAGE_EVIDENCE_GUIDANCE
    source = "猫"
    candidate = {"segments": [{"surface": source}], "grammar_overlays": []}
    review = {"verdict": "revise", "issues": [{"problem": "meaning"}]}
    story_plan = {"terms": []}
    (tmp_path / "story-vocabulary-plan.json").write_text(json.dumps(story_plan))
    components = []
    for suffix in ("review", "boundary_review"):
        job = f"annotations/chunk_0000/adjudicated_final_01_{suffix}"
        job_dir = tmp_path / "agents" / job
        job_dir.mkdir(parents=True, exist_ok=True)
        raw_review = {"verdict": "revise", "issues": [{"problem": suffix}]}
        (job_dir / "meta.json").write_text(json.dumps({
            "fingerprint": f"input-{suffix}", "return_code": 0,
            "model": "gpt-6-luna", "effort": "low", "tool_profile": "offline"}))
        (job_dir / "result.json").write_text(json.dumps(raw_review))
        child = bind_review_job(tmp_path, job, candidate=candidate, source_text=source)
        child.update(candidate_digest=evidence_digest(candidate),
                     source_digest=evidence_digest(source))
        components.append(child)

    grammar = japanese_semantic_repair_grammar_knowledge()
    context = {"chunk_text": source, "grammar_knowledge": grammar,
               "story_plan": story_plan}
    references = {
        "approved-grammar": {"kind": "approved_lesson",
                             "content": grammar.get("approved_entries", [])},
        "review-policy": {"kind": "explicit_review_policy",
            "content": {"form_stage_guidance": FORM_STAGE_EVIDENCE_GUIDANCE,
                "review_policy": "", "dictionary_policy": JAPANESE_ANNOTATION_CHUNK_POLICY}},
    }
    receipt = {"kind": "composite", "review_digest": evidence_digest(review),
               "components": components}
    gate = {"passed": True, "issues": [],
            "candidate_digest": evidence_digest(candidate),
            "source_text_digest": evidence_digest(source)}
    replay = {"prior_history": [], "context": context,
              "known_reference_input": references,
              "deterministic_gate_evidence": gate,
              "normal_review_receipt": receipt}
    row = {"stage": "adjudicated_final_01", "annotation": candidate,
           "review": review, "adjudication": {"status": "cleared", "approved": True},
           "effective_review": {"kind": "adjudicated"},
           "adjudication_replay": replay}
    item = {"segments": candidate["segments"], "grammar_overlays": [],
            "attempts": [row], "resolved": True,
            "effective_review": {"kind": "adjudicated"}}
    monkeypatch.setattr(CodexRunner, "_check_tool_profile", staticmethod(lambda *_args: None))
    monkeypatch.setattr(annotation_adjudication, "verify_adjudication_evidence",
        lambda *_args, **_kwargs: {"status": "cleared", "approved": True})
    return source, item
