import { useRef, useState } from "react";
import type { NeuralExecution } from "./demo-contract";
import type { LoadedNeuralResult } from "./load-neural-result";
import type { NeuralPerceptionResult } from "./neural-perception";
import NeuralPresenter from "./NeuralPresenter";
import NeuralPerceptionPanel from "./NeuralPerceptionPanel";
export default function NeuralReview({ execution, writable, onFrameChange }: { execution: NeuralExecution; writable: boolean; onFrameChange: (index: number) => void }) {
  const viewer = useRef<HTMLDivElement>(null);
  const [loaded, setLoaded] = useState<LoadedNeuralResult | null>(null), [comparison, setComparison] = useState<NeuralPerceptionResult | null>(null), [displayedFrame, setDisplayedFrame] = useState(0);
  const [inspection, setInspection] = useState<{ frame_index: number; nonce: number } | null>(null);
  return <><div ref={viewer}><NeuralPresenter execution={execution} onFrameChange={onFrameChange} onLoaded={setLoaded} comparisonResult={comparison} inspection={inspection} onDisplayedFrame={setDisplayedFrame}/></div>{loaded && <NeuralPerceptionPanel execution={execution} loaded={loaded} writable={writable} displayedFrame={displayedFrame} onResult={setComparison} onInspect={frame_index => { setInspection(previous => ({ frame_index, nonce: (previous?.nonce ?? 0) + 1 })); viewer.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" }); }}/>}</>;
}
