import * as React from 'react';
import * as ReactDom from 'react-dom';
import { BaseClientSideWebPart } from '@microsoft/sp-webpart-base';
import { SPHttpClient } from '@microsoft/sp-http';
import EndpointIq from './components/EndpointIq';
import { Snapshot } from './components/model';

export default class EndpointIqWebPart extends BaseClientSideWebPart<{ colorScheme?: 'teal' | 'blue' }> {
  public render(): void {
    ReactDom.render(React.createElement(EndpointIq, {
      colorScheme: this.properties.colorScheme,
      loadSnapshot: async (): Promise<Snapshot> => {
        const root = this.context.pageContext.web.serverRelativeUrl.replace(/\/$/, '');
        const response = await this.context.spHttpClient.get(
          `${root}/SiteAssets/WISEEndpointIntelligence/dashboard.json`, SPHttpClient.configurations.v1,
          { headers: { Accept: 'application/json' } }
        );
        if (!response.ok) throw new Error(`Report could not be loaded (HTTP ${response.status}).`);
        return response.json();
      }
    }), this.domElement);
  }

  protected onDispose(): void {
    ReactDom.unmountComponentAtNode(this.domElement);
  }
}