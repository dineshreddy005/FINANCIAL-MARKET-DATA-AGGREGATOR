import { useEffect, useRef, useState } from 'react';

/**
 * One character cell of a split-flap (departure-board) display. The front
 * face holds the OLD value and rotates away; the back face holds the NEW
 * value and is revealed underneath -- the same mechanism as an airport
 * board or a real stock-ticker flap. `frontChar` only catches up to the
 * latest value once the flip's rotation finishes, which is what makes the
 * animation read as "the flap settling" rather than an instant cut.
 */
function FlipDigit({ char }) {
  const [frontChar, setFrontChar] = useState(char);
  const [backChar, setBackChar] = useState(char);
  const [flipping, setFlipping] = useState(false);
  const timeoutRef = useRef(null);

  useEffect(() => {
    if (char === frontChar) return undefined;
    setBackChar(char);
    setFlipping(true);
    clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      setFrontChar(char);
      setFlipping(false);
    }, 360);
    return () => clearTimeout(timeoutRef.current);
    // frontChar is intentionally excluded -- this effect should only react
    // to the incoming value changing, not to its own settling state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [char]);

  return (
    <span className={`flip-digit ${flipping ? 'flip-digit--flipping' : ''}`}>
      <span className="flip-digit__face flip-digit__face--front">{frontChar}</span>
      <span className="flip-digit__face flip-digit__face--back">{backChar}</span>
    </span>
  );
}

/**
 * Renders a formatted number/string as a row of split-flap cells. Only
 * digits flip; punctuation (`.`, `,`, `$`) is rendered as a static
 * character so the animation reads as digits updating, not the whole
 * string re-flowing.
 */
export default function FlipNumber({ value, className = '' }) {
  const chars = String(value).split('');
  return (
    <span className={`flip-number ${className}`}>
      {chars.map((c, i) =>
        /[0-9]/.test(c) ? (
          <FlipDigit key={i} char={c} />
        ) : (
          <span key={i} className="flip-number__static">
            {c}
          </span>
        )
      )}
    </span>
  );
}
