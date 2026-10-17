# Close Call

A warehouse safety lead asks one question and gets the few seconds that matter, with a fingerprint so someone else can check the words were not changed.

VAST Builders Challenge · London · 17 October 2026

The one-page brief is [close-call.pdf](close-call.pdf).

## Who uses it

A safety lead on a site that already has aisle cameras. They are accountable for injuries. They do not watch the full recording.

## The problem

A shift produces hours of video. The useful part is a person standing next to a moving forklift. Finding that by hand means scrubbing the tape. A summary sent to a supervisor or an insurer has to stay checkable after it is sent.

## What the page does

They type “person close to a moving forklift.” The page returns a short list of cards. Each card has the clip, the start and end time, the camera, and the sentence the video model wrote. A button reloads that clip and checks a fingerprint of the camera, the times, and the sentence. If the words were changed, the check fails.

## Demo

Search the warehouse camera. Open one card. Read the sentence next to the frames. Press verify. The fingerprint matches the clip still in the index.

## How it is built

The footage is already indexed. Search runs on Cosmos captions and embeddings. YOLO has already marked people and forklifts. If the existing captions never mention a person near a forklift, one warehouse chunk is re-ingested with a prompt that asks only for what is visible: who is in frame, whether the forklift is moving, and whether a person is close to it. The page calls search and plays the clip. The fingerprint is a hash of that result.

## Left out

No claim that a person is an AI. No phone-call scoring. No second camera unless the warehouse cards already work.

## What you keep

Tells scores a voice on a call and cannot yet show the evidence under the score. This card is that pattern on video: a claim, the exact sentence it came from, and a check.

## Deploy

On the lab VM:

```bash
git clone https://github.com/ch4r10t33r/vastdata-hackathon-team5.git
cd vastdata-hackathon-team5
bash tools/close-call/deploy.sh
```

Then open https://workshop.thecosmoslabs.com and click **App**. Search for `person close to a moving forklift` on camera `sdg_warehouse_cam-2`. If the captions never mention a person near a forklift, re-ingest one warehouse chunk before searching again.
