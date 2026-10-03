#!/usr/bin/env python3
"""Plays the non-Slack beats of the doctor-out-sick story on cue, reacting to the real events.
Darsana types in Slack; this script does Bob's replies, check-in, the planted document and the packet."""
import json, os, sys, time, urllib.request

B = "http://localhost:8090"


def get(path):
    return json.load(urllib.request.urlopen(B + path, timeout=30))


def post(path, body=None):
    req = urllib.request.Request(B + path, data=json.dumps(body or {}).encode(), headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def say(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def wait_for(check, what, timeout=600):
    say("waiting: " + what)
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = check()
        if r:
            return r
        time.sleep(1)
    say("TIMEOUT: " + what); sys.exit(1)


def kinds():
    return {e["kind"] for e in get("/api/story")["events"]}


def bob_appt():
    s = get("/api/story")["schedule"]
    return next((a["id"] for a in s if a["patient"] == "Bob Testwell" and a["status"] in ("confirmed", "booked", "checked_in")), None)


say("CUE Darsana (Slack): I'm out sick tomorrow, please reschedule my appointments")
wait_for(lambda: "provider.outage" in kinds(), "doctor's outage message")
time.sleep(8)
say("Bob replies YES on his phone"); post("/api/sim/patient-reply", {"patient_id": "P-108", "body": "Yes"})
wait_for(lambda: "reschedule.verified" in kinds() or any("verified by fresh read" in e["summary"] for e in get("/api/story")["events"]), "verified move")
time.sleep(5)
aid = bob_appt(); say(f"Next morning: Bob checks in ({aid})"); post(f"/api/visits/{aid}/checkin")
time.sleep(4)
say("CUE Darsana (Slack): Send Metformin 500 mg twice daily for patient Bob Testwell   -> then: confirm")
wait_for(lambda: any(o["patient_id"] == "P-108" and o["kind"] == "rx" and o["status"] == "transmitted" for o in get("/api/state")["orders"]),
         "Bob's prescription sent to CVS", timeout=900)
time.sleep(4)
if "--synthetic-dictation" in sys.argv:
    say("Doctor's dictation (synthetic voice, NVIDIA FastPitch on the GB10) -> Parakeet + Sortformer -> note draft")
    text = ("Visit note for Bob Testwell, patient P-108. Bob is doing well. His blood sugar is improving on diet changes. "
            "Continue Metformin 500 milligrams twice daily. Follow up in three months.")
    wav = urllib.request.urlopen(urllib.request.Request(B + "/api/voice/speak", data=json.dumps({"text": text}).encode(),
                                                        headers={"content-type": "application/json"}), timeout=120).read()
    bnd = "cadence-boundary"
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"message\"\r\n\r\nP-108\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"notify\"\r\n\r\n1\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"dictation.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{bnd}--\r\n".encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(B + "/api/sim/voice", data=body,
                 headers={"content-type": f"multipart/form-data; boundary={bnd}"}), timeout=300))
    say(f"note {r.get('document')} drafted; CUE Darsana (Slack): release")
else:
    say("CUE (on the GB10): click 🎙 Dictate, speak the visit note, click Stop   -> Darsana replies RELEASE D-... in Slack")
wait_for(lambda: any(d["patient_id"] == "P-108" and d["kind"] == "visit_note" and d["status"] == "released" for d in get("/api/state")["documents"]),
         "visit note released by the doctor", timeout=900)
time.sleep(4)
say("A referral letter arrives with a planted instruction"); post("/api/demo/upload")
time.sleep(8)
say("Coordinator handoff: review packet"); r = post("/api/demo/packet")
say(f"packet {r.get('packet_id')}: {r.get('status')} gaps={r.get('gaps')}")
time.sleep(10)
say("DONE")
