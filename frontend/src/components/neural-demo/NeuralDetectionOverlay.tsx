import type { NeuralPrediction, NeuralSide } from "./neural-perception";
const colors = { original: "#71d6ed", baseline: "#b3eba1", novel: "#ffce75" };
export default function NeuralDetectionOverlay({ prediction, side }: { prediction: NeuralPrediction | undefined; side: NeuralSide }) {
  if (!prediction) return null;
  const { width, height } = prediction.image, color = colors[side];
  return <svg className="neural-detection-overlay" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="xMidYMid meet" aria-label={`${side} 모델 검출 ${prediction.detections.length}개`}>
    {prediction.detections.map((detection, index) => {
      const [x1, y1, x2, y2] = detection.bbox_xyxy, label = `${detection.label} ${detection.score.toFixed(2)}`, textWidth = Math.min(width, label.length * 5.6 + 9), labelX = Math.min(x1, width - textWidth), labelY = Math.max(0, y1 - 15);
      return <g key={`${detection.id}-${index}`}><rect x={x1} y={y1} width={x2 - x1} height={y2 - y1} fill="none" stroke={color} strokeWidth={1.6}/><rect x={labelX} y={labelY} width={textWidth} height={14} rx={2} fill={color} fillOpacity={.94}/><text x={labelX + 4} y={labelY + 10} fill="#102017" fontSize={10} fontFamily="Arial,sans-serif" fontWeight={600}>{label}</text></g>;
    })}
  </svg>;
}
