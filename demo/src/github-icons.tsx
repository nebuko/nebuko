import React from 'react';

/* Icons drawn from primitives (approximations of Octicons, no emoji, so they
 * render identically on any machine). They inherit `currentColor`. */
export type IconProps = {size?: number; color?: string; style?: React.CSSProperties};

const Svg: React.FC<IconProps & {children: React.ReactNode; fill?: boolean}> = ({
  size = 16,
  color = 'currentColor',
  style,
  fill,
  children,
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 16 16"
    fill={fill ? 'currentColor' : 'none'}
    stroke={fill ? 'none' : 'currentColor'}
    strokeWidth={1.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{color, flexShrink: 0, display: 'block', ...style}}
  >
    {children}
  </svg>
);

export const IconIssueOpened: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <circle cx="8" cy="8" r="1.3" fill="currentColor" stroke="none" />
  </Svg>
);
export const IconPullRequest: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="4" cy="3.5" r="1.5" />
    <circle cx="4" cy="12.5" r="1.5" />
    <circle cx="12" cy="12.5" r="1.5" />
    <path d="M4 5v6M12 11V7.5a2 2 0 0 0-2-2H8.5M10 3.5 8.5 5.5 10 7.5" />
  </Svg>
);
export const IconMerge: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="4" cy="3.5" r="1.5" />
    <circle cx="4" cy="12.5" r="1.5" />
    <circle cx="12" cy="8" r="1.5" />
    <path d="M4 5v6M4 5c0 2.6 2 3 6.5 3" />
  </Svg>
);
export const IconCommit: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="2.6" />
    <path d="M1.5 8h3.9M10.6 8h3.9" />
  </Svg>
);
export const IconCheck: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M3 8.5 6.2 11.7 13 4.6" />
  </Svg>
);
export const IconX: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </Svg>
);
export const IconCheckCircle: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M5.2 8.2 7.2 10.2 10.9 6" />
  </Svg>
);
export const IconXCircle: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" />
  </Svg>
);
export const IconDot: React.FC<IconProps> = (p) => (
  <Svg {...p} fill>
    <circle cx="8" cy="8" r="4" />
  </Svg>
);
export const IconKebab: React.FC<IconProps> = (p) => (
  <Svg {...p} fill>
    <circle cx="3" cy="8" r="1.5" />
    <circle cx="8" cy="8" r="1.5" />
    <circle cx="13" cy="8" r="1.5" />
  </Svg>
);
export const IconSmiley: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M5.4 9.6c.6 1 1.5 1.5 2.6 1.5s2-.5 2.6-1.5" />
    <circle cx="5.9" cy="6.3" r=".6" fill="currentColor" stroke="none" />
    <circle cx="10.1" cy="6.3" r=".6" fill="currentColor" stroke="none" />
  </Svg>
);
export const IconChevronRight: React.FC<IconProps> = (p) => (
  <Svg {...p} fill>
    <path d="M6 3.8 10.6 8 6 12.2z" />
  </Svg>
);
export const IconChevronDown: React.FC<IconProps> = (p) => (
  <Svg {...p} fill>
    <path d="M3.8 6 8 10.6 12.2 6z" />
  </Svg>
);
export const IconGear: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="2.2" />
    {[0, 45, 90, 135].map((a) => (
      <g key={a} transform={`rotate(${a} 8 8)`}>
        <path d="M8 1.6v2.2M8 12.2v2.2" />
      </g>
    ))}
  </Svg>
);
export const IconPin: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M9.5 1.8 14.2 6.5 12.6 8l-.8-.3-2 2 .2 2-1.1 1.1-2.7-2.7L3 13.5M6.2 5.4l1.6-1.6" />
  </Svg>
);
export const IconTag: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M1.8 2.8v4.4l6.6 6.6a1 1 0 0 0 1.4 0l4-4a1 1 0 0 0 0-1.4L7.2 1.8H2.8a1 1 0 0 0-1 1z" />
    <circle cx="5" cy="5" r=".8" fill="currentColor" stroke="none" />
  </Svg>
);
export const IconSearch: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="6.8" cy="6.8" r="4.6" />
    <path d="m10.3 10.3 4 4" />
  </Svg>
);
export const IconPlus: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M8 2.5v11M2.5 8h11" />
  </Svg>
);
export const IconBell: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M3.4 11.2h9.2l-1-1.6V6.4a3.6 3.6 0 0 0-7.2 0v3.2zM6.6 13.4a1.5 1.5 0 0 0 2.8 0" />
  </Svg>
);
export const IconMenu: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M2 4h12M2 8h12M2 12h12" />
  </Svg>
);
export const IconCode: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="m5.5 4.5-3.5 3.5 3.5 3.5M10.5 4.5 14 8l-3.5 3.5" />
  </Svg>
);
export const IconPlay: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M6.6 5.6v4.8L10.6 8z" fill="currentColor" />
  </Svg>
);
export const IconTable: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <rect x="1.8" y="2.5" width="12.4" height="11" rx="1.6" />
    <path d="M1.8 6.2h12.4M6 6.2v7.3" />
  </Svg>
);
export const IconShield: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M8 1.6 13.4 3.6V8c0 3-2.4 5-5.4 6.4C5 13 2.6 11 2.6 8V3.6z" />
  </Svg>
);
export const IconGraph: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M2 2v12h12M5.5 11V8M8.5 11V5M11.5 11V7" />
  </Svg>
);
export const IconRepo: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M3 12.2V3.3A1.3 1.3 0 0 1 4.3 2H13v10.2H4.4A1.4 1.4 0 0 0 3 13.6M3 13.6A1.4 1.4 0 0 0 4.4 15H13v-2.8" />
  </Svg>
);
export const IconStar: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="m8 1.8 1.9 4 4.3.5-3.2 3 .9 4.3L8 11.4l-3.9 2.2.9-4.3-3.2-3 4.3-.5z" />
  </Svg>
);
export const IconInfo: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M8 7.2v4M8 4.9v.1" />
  </Svg>
);
export const IconWarning: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M8 2 14.4 13H1.6z" />
    <path d="M8 6.4v3M8 11.2v.1" />
  </Svg>
);
export const IconReport: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <rect x="1.8" y="2.2" width="12.4" height="9.4" rx="1.6" />
    <path d="M5 11.6v2.6l3-2.6M8 4.8v3M8 9v.1" />
  </Svg>
);
export const IconComment: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M2 3.2h12v7.2H8.6L5.4 13.4v-3H2z" />
  </Svg>
);
export const IconFileDiff: React.FC<IconProps> = (p) => (
  <Svg {...p}>
    <path d="M3.5 1.8h6l3 3v9.4h-9zM9.5 1.8v3h3M6 8.2h4M8 6.4v3.6M6 12h4" />
  </Svg>
);
export const IconMark: React.FC<IconProps> = ({size = 32, color = 'currentColor', style}) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill={color} style={style}>
    <path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.84 1.13.19.43.6.81 1.55.55 0 .94.01 1.8.01 2.04 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z" />
  </svg>
);
