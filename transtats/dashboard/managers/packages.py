# Copyright 2016 Red Hat, Inc.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import operator
from collections import OrderedDict

from ..models.package import Packages
from ..models.transplatform import TransPlatform
from .base import BaseManager
from .settings import AppSettingsManager
from .syncstats import SyncStatsManager


class PackagesManager(BaseManager):
    """
    Packages Manager
    """

    def get_packages(self, pkgs=None):
        """
        fetch packages from db
        """
        packages = None
        try:
            packages = self.db_session.query(Packages) \
                .filter(Packages.package_name.in_(pkgs)) \
                .order_by('transtats_lastupdated').all() if pkgs else \
                self.db_session.query(Packages).order_by('transtats_lastupdated').all()
        except:
            self.db_session.rollback()
            # log event, passing for now
            pass
        return packages

    def add_package(self, **kwargs):
        """
        add package to db
        :param kwargs: dict
        :return: boolean
        """
        required_params = ('package_name', 'upstream_url', 'transplatform_slug', 'release_streams')
        if not set(required_params) < set(kwargs.keys()):
            return

        if not (kwargs['package_name'] and kwargs['upstream_url']):
            return

        try:
            # todo
            # fetch project details from transplatform and save in db

            # derive transplatform project URL
            platform_url = self.db_session.query(TransPlatform.api_url). \
                filter_by(platform_slug=kwargs['transplatform_slug']).one()[0]
            kwargs['transplatform_url'] = platform_url + "/project/view/" + kwargs['package_name']
            kwargs['lang_set'] = 'default'
            # save in db
            new_package = Packages(**kwargs)
            self.db_session.add(new_package)
            self.db_session.commit()
        except:
            self.db_session.rollback()
            # log event, pass for now
            return False
        else:
            return True

    def _get_project_ids_names(self, projects):
        ids = []
        names = []
        for project in projects:
            ids.append(project['id'])
            names.append(project['name'])
        return ids, names

    def validate_package(self, **kwargs):
        """
        Validates existence of a package at a transplatform
        :param kwargs: dict
        :return: str, Boolean
        """
        if not (kwargs.get('package_name')):
            return
        package_name = kwargs['package_name']
        # get transplatform projects from db
        platform = self.db_session.query(
            TransPlatform.engine_name, TransPlatform.api_url,
            TransPlatform.projects_json). \
            filter_by(platform_slug=kwargs['transplatform_slug']).one()
        projects_json = platform.projects_json
        # if not found in db, fetch transplatform projects from API
        if not projects_json:
            rest_handle = self.rest_client(platform.engine_name, platform.api_url)
            response_dict = rest_handle.process_request('list_projects')
            if response_dict and response_dict.get('json_content'):
                projects_json = response_dict['json_content']

        ids, names = self._get_project_ids_names(projects_json)
        if package_name in ids:
            return package_name
        elif package_name in names:
            return ids[names.index(package_name)]
        else:
            return False

    def _extract_locale_translated(self, stats_dict_list):
        locale_translated = []
        for stats_dict in stats_dict_list:
            translation_percent = \
                round((stats_dict.get('translated') * 100) / stats_dict.get('total'), 2) \
                if stats_dict.get('total') > 0 else 0
            locale_translated.append([stats_dict.get('locale'), translation_percent])
        return locale_translated

    def _format_stats_for_graphs(self, locale_sequence, stats_dict):
        stats_for_graphs_dict = {}
        stats_for_graphs_dict['ticks'] = \
            [[i, lang] for i, lang in enumerate(locale_sequence.values(), 0)]

        stats_for_graphs_dict['graph_data'] = {}
        for version, stats_lists in stats_dict.items():
            new_stats_list = []
            for stats_tuple in stats_lists:
                locale = stats_tuple[0].replace('-', '_') if ('-' in stats_tuple[0]) else stats_tuple[0]
                index = [i for i, locale_tuple in enumerate(list(locale_sequence), 0) if locale in locale_tuple]
                index.append(stats_tuple[1])
                new_stats_list.append(index)
            stats_for_graphs_dict['graph_data'][version] = new_stats_list

        return stats_for_graphs_dict

    def get_trans_stats(self, package_name):
        """
        fetch stats of a package for all enabled languages
        :param package_name: str
        :return: dict {project_version: stats_dict}
        """
        trans_stats_dict = OrderedDict()
        # 1st, get active locales for which stats are to be shown
        active_locales = AppSettingsManager(self).get_locales_set()[0]
        lang_id_name = {(lang.locale_id, lang.locale_alias): lang.lang_name
                        for lang in active_locales}
        lang_id_name = OrderedDict(sorted(lang_id_name.items(), key=operator.itemgetter(1)))

        # 2nd, filter stats json for required locales
        package_details = self.get_packages([package_name])[0]  # this must be a list
        if not package_details.transtats_lastupdated:
            return trans_stats_dict
        syncstats_manager = SyncStatsManager(self)
        pkg_stats_versions = syncstats_manager.get_sync_stats([package_name])
        for pkg_stats_version in pkg_stats_versions:
            trans_stats_list, missing_locales = \
                syncstats_manager.filter_stats_for_required_locales(
                    pkg_stats_version.stats_raw_json, list(lang_id_name)
                )
            trans_stats_dict[pkg_stats_version.project_version] = \
                self._extract_locale_translated(trans_stats_list)
        # 3rd, format trans_stats_list for graphs
        return self._format_stats_for_graphs(lang_id_name, trans_stats_dict)
