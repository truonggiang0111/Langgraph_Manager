import { Action } from '../types';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { ScrollArea } from './ui/scroll-area';
import { ChevronRight } from 'lucide-react';
import { Button } from './ui/button';
import { FacebookResearchResult } from './FacebookResearchResult';

interface InspectorPanelProps {
  selectedAction: Action | null;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

export function InspectorPanel({ selectedAction, collapsed, onToggleCollapse }: InspectorPanelProps) {
  if (collapsed) {
    return (
      <div className="w-10 bg-background border-l border-gray-200 flex flex-col items-center py-4">
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 hover:bg-gray-300"
          onClick={onToggleCollapse}
        >
          <ChevronRight className="h-3.5 w-3.5 rotate-180 text-gray-600" />
        </Button>
      </div>
    );
  }

  if (!selectedAction) {
    return (
      <div className="w-80 bg-white border-l border-gray-200 flex flex-col">
        <div className="p-3 border-b border-gray-200 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">Inspector</h2>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0 hover:bg-gray-100"
            onClick={onToggleCollapse}
          >
            <ChevronRight className="h-3.5 w-3.5 text-gray-600" />
          </Button>
        </div>
        <div className="flex-1 flex items-center justify-center p-6 bg-background">
          <div className="text-center">
            <div className="mb-2 h-10 w-10 rounded-full bg-gray-200 flex items-center justify-center mx-auto">
              <span className="text-xl">🔍</span>
            </div>
            <p className="text-xs text-gray-500">
              Select an action to view details
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-80 bg-white border-l border-gray-200 flex flex-col">
      <div className="p-3 border-b border-gray-200 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-900">Inspector</h2>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 w-7 p-0 hover:bg-gray-100"
          onClick={onToggleCollapse}
        >
          <ChevronRight className="h-3.5 w-3.5 text-gray-600" />
        </Button>
      </div>

      <Tabs defaultValue="result" className="flex-1 flex flex-col">
        <div className="px-3 pt-3">
          <TabsList className="grid w-full grid-cols-2 h-8">
            <TabsTrigger value="result" className="text-xs">Result</TabsTrigger>
            <TabsTrigger value="logs" className="text-xs">Logs</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="result" className="flex-1 m-0">
          <ScrollArea className="h-full">
            <div className="p-3 space-y-3">
              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Action</h3>
                <p className="text-sm text-gray-600">{selectedAction.kind}</p>
              </div>

              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Description</h3>
                <p className="text-sm text-gray-600">{selectedAction.description}</p>
              </div>

              {selectedAction.facebookResearch ? (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Facebook Research</h3>
                  <FacebookResearchResult data={selectedAction.facebookResearch} />
                </div>
              ) : selectedAction.result && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Result</h3>
                  <div className="bg-gray-50 rounded-md p-3 text-sm text-gray-700">
                    {selectedAction.result}
                  </div>
                </div>
              )}

              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Status</h3>
                <p className="text-sm text-gray-600 capitalize">{selectedAction.status}</p>
              </div>

              {(typeof selectedAction.hardGateOk === 'boolean' || (selectedAction.missingRequirements?.length || 0) > 0) && (
                <div>
                  <h3 className="text-sm font-medium text-gray-700 mb-2">Verification Gate</h3>
                  <div className="bg-gray-50 rounded-md p-3 text-sm text-gray-700 space-y-2">
                    {typeof selectedAction.hardGateOk === 'boolean' && (
                      <p>
                        Hard gate:{' '}
                        <span className={selectedAction.hardGateOk ? 'text-green-600 font-medium' : 'text-red-600 font-medium'}>
                          {selectedAction.hardGateOk ? 'OK' : 'FAILED'}
                        </span>
                      </p>
                    )}
                    <p>
                      Missing requirements:{' '}
                      {(selectedAction.missingRequirements?.length || 0) > 0
                        ? selectedAction.missingRequirements!.join(', ')
                        : 'None'}
                    </p>
                  </div>
                </div>
              )}

              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Risk Level</h3>
                <p className="text-sm text-gray-600 capitalize">{selectedAction.risk}</p>
              </div>

              <div>
                <h3 className="text-sm font-medium text-gray-700 mb-2">Timestamp</h3>
                <p className="text-sm text-gray-600">
                  {selectedAction.timestamp.toLocaleString()}
                </p>
              </div>
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="logs" className="flex-1 m-0">
          <ScrollArea className="h-full">
            <div className="p-3">
              {selectedAction.logs && selectedAction.logs.length > 0 ? (
                <div className="bg-gray-900 rounded-md p-2 font-mono text-xs text-gray-100 space-y-1">
                  {selectedAction.logs.map((log, i) => (
                    <div key={i}>{log}</div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8">
                  <p className="text-xs text-gray-500">No logs available</p>
                </div>
              )}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </div>
  );
}
