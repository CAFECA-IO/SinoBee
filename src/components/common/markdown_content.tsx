import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface IMarkdownContentProps {
  content: string;
  theme?: 'dark' | 'light';
}

const MarkdownContent: React.FC<IMarkdownContentProps> = ({ content, theme = 'dark' }) => {
  const isDark = theme === 'dark';
  const textColor = isDark ? 'text-white' : 'text-gray-900';
  const secondaryTextColor = isDark ? 'text-[#E0E0E0]' : 'text-gray-700';
  const accentColor = '#FF9800';
  const linkColor = isDark ? 'text-[#64B5F6]' : 'text-blue-600';
  const borderColor = isDark ? 'border-[#444]' : 'border-gray-200';
  const blockquoteBg = isDark ? 'bg-[#FF9800]/10' : 'bg-orange-50';
  const blockquoteText = isDark ? 'text-[#FFE0B2]' : 'text-orange-800';
  const tableBorder = isDark ? 'border-[#444]' : 'border-gray-300';
  const theadBg = isDark ? 'bg-white/5' : 'bg-gray-50';
  const thText = isDark ? 'text-[#FFB74D]' : 'text-orange-700';
  const rowBorder = isDark ? 'border-[#333]' : 'border-gray-200';

  const result = (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h1: ({ children, ...props }: React.ComponentPropsWithoutRef<'h1'>) => (
          <h1
            className={`mb-3 mt-5 flex items-center gap-2 border-b ${borderColor} pb-2 text-[1.4rem] ${textColor}`}
            {...props}
          >
            {children}
          </h1>
        ),
        h2: ({ children, ...props }: React.ComponentPropsWithoutRef<'h2'>) => (
          <h2 className={`mb-2 mt-4 flex items-center gap-2 text-[1.2rem] ${textColor}`} {...props}>
            <span className={`inline-block h-[18px] w-1 rounded-sm bg-[${accentColor}]`}></span>
            {children}
          </h2>
        ),
        h3: ({ children, ...props }: React.ComponentPropsWithoutRef<'h3'>) => (
          <h3 className={`mb-1.5 mt-3 text-[1.1rem] font-semibold ${textColor}`} {...props}>
            {children}
          </h3>
        ),
        strong: ({ children, ...props }: React.ComponentPropsWithoutRef<'strong'>) => (
          <strong className={textColor} {...props}>
            {children}
          </strong>
        ),
        ul: ({ children, ...props }: React.ComponentPropsWithoutRef<'ul'>) => (
          <ul className="list-none pl-5" {...props}>
            {children}
          </ul>
        ),
        ol: ({ children, ...props }: React.ComponentPropsWithoutRef<'ol'>) => (
          <ol className="list-decimal pl-5" {...props}>
            {children}
          </ol>
        ),
        li: ({
          children,
          ...props
        }: React.ComponentPropsWithoutRef<'li'> & { ordered?: boolean }) => {
          const { ordered, ...rest } = props;
          if (ordered) {
            return (
              <li className={`mb-1.5 pl-1 ${secondaryTextColor}`} {...rest}>
                {children}
              </li>
            );
          }
          return (
            <li className={`relative mb-1.5 pl-0 ${secondaryTextColor}`} {...rest}>
              {/* Info: (20251220 - Luphia) 小圓裝飾點 */}
              {/* <span className="absolute -left-[25px] top-[7px] size-1.5 bg-[#FF9800] rounded-full"></span> */}
              {children}
            </li>
          );
        },
        p: ({ children, ...props }: React.ComponentPropsWithoutRef<'p'>) => (
          <p className={`mb-2.5 leading-relaxed ${secondaryTextColor}`} {...props}>
            {children}
          </p>
        ),
        a: ({ children, ...props }: React.ComponentPropsWithoutRef<'a'>) => (
          <a className={`${linkColor} underline`} {...props}>
            {children}
          </a>
        ),
        blockquote: ({ children, ...props }: React.ComponentPropsWithoutRef<'blockquote'>) => (
          <blockquote
            className={`my-2.5 rounded border-l-4 border-[${accentColor}] ${blockquoteBg} px-3.5 py-2.5 italic ${blockquoteText}`}
            {...props}
          >
            {children}
          </blockquote>
        ),
        table: ({ children, ...props }: React.ComponentPropsWithoutRef<'table'>) => (
          <div className="my-5 overflow-x-auto">
            <table className={`w-full border-collapse border ${tableBorder} text-sm`} {...props}>
              {children}
            </table>
          </div>
        ),
        thead: ({ children, ...props }: React.ComponentPropsWithoutRef<'thead'>) => (
          <thead className={theadBg} {...props}>
            {children}
          </thead>
        ),
        tbody: ({ children, ...props }: React.ComponentPropsWithoutRef<'tbody'>) => (
          <tbody {...props}>{children}</tbody>
        ),
        tr: ({ children, ...props }: React.ComponentPropsWithoutRef<'tr'>) => (
          <tr className={`border-b ${rowBorder}`} {...props}>
            {children}
          </tr>
        ),
        th: ({ children, ...props }: React.ComponentPropsWithoutRef<'th'>) => (
          <th
            className={`border-r ${tableBorder} p-3 text-left font-semibold ${thText}`}
            {...props}
          >
            {children}
          </th>
        ),
        td: ({ children, ...props }: React.ComponentPropsWithoutRef<'td'>) => (
          <td className={`border-r ${tableBorder} p-3 text-left ${secondaryTextColor}`} {...props}>
            {children}
          </td>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );

  return result;
};

export { MarkdownContent };
