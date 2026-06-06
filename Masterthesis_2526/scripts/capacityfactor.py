"""
This script:
- calculates the capacity factor (CF) and the equivalent full load hours (EFLH) of an engine
- generates a LaTeX file that contains:
  - a table to include in a report
  - a frame to include in a presentation 
"""

from   pathlib           import Path         # fetch file name      docs.python.org/3.9/library/pathlib.html

## INPUT
# engine
P               = 39       # kW     nominal power
E               = 90.4     # MWh/y  electricity production
#constants
n               = 8760     # h/y    number of hours per year

## CALCULATION
# capacity factor
Emax            = P * n / 1e3    # MWh/y   maximum electricity production in a year
CF              = E / Emax       # -       capacity factor                                                
# full load hours
F               = E * 1e3 / P    # h      number of equivalent full load hours

## OUTPUT to LaTeX
# content for report
outputtable = r"""
\begin{table}\centering
    \caption{Example of a table generated in Python}\label{tab:pythontable}
    \begin{tabular}{lrl}  
        \toprule
        parameter               &  value            & unit              \\[1ex]
        \textit{inputs:}        &                   &                   \\
        nominal power           &  \num{%.0f}       & \unit{kW}         \\
        electricity produced    &  \num{%.1f}       & \unit{MWh/year}   \\[1ex]
        \textit{calculated:}    &                   &                   \\
        capacity factor         &  \num{%.1f}       & \unit{\percent}   \\
        equiv. full load hours  &  \num{%.0f}       & \unit{\h}         \\
        \bottomrule
    \end{tabular}
\end{table}
"""

# content for slides
outputslide = r"""
\begin{frame}<presentation>{Example of a slide generated in Python (with script %s.py)}
An engine with a nominal power of \qty{%.0f}{\kW} produces \qty{%.1f}{\MWh} of electricity in a year.
Calculate the capacity factor (CF) and the number of equivalent full load hours (EFLH).

\begin{align*}
    \intertext{The capacity factor}
    &\up{CF} = \frac{\qty{%.0f}{\MWh\per\yr}}{\qty{%.0f}{\kW}\cdot\qty{8760}{\h\per\yr}}=\qty{%.1f}{\percent} 
    \intertext{Equivalent hours of full load operation}
    &\up{EFLH} = \frac{\qty{%.0f}{\MWh\per\yr}}{\qty{%.0f}{\kW}}=\qty{%.0f}{\h} 
\end{align*}
\end{frame}
"""

# write content to tex file
script_name = Path(__file__).stem                                                 # get file name of this script without the extension .py
tex_file_name = f"{script_name}.tex"                                              # make tex file with the same name
tex_header    = f"% This file was created with the script {script_name}.py\n"     # header with comment for tex file
mode_header   = "\mode* % only put content inside frame environments on slides\n" # header with \mode* instruction
with open(tex_file_name, "w") as file:                                            # open tex file for writing
     file.write(tex_header)                                                       # write header with comment to tex file
     file.write(mode_header)                                                      # write \mode* instruction to tex file    
     file.write(outputtable % (P,E,CF*100,F))                                     # write content for report to tex file
     file.write(outputslide % (script_name,P,E,E,P,CF*100,E,P,F))                             # write content for slides to tex file
     # add more write instruction if you have more content









