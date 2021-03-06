import os

import requests
import globus_sdk
from tqdm import tqdm

from mdf_forge import toolbox

# Maximum recommended number of HTTP file transfers
# Large transfers are much better suited to Globus Transfer use
HTTP_NUM_LIMIT = 10
# Maximum number of results per search allowed by Globus Search
SEARCH_LIMIT = 10000


class Forge:
    """Fetch metadata from Globus Search and files from the Materials Data Facility.
    Forge is intended to be the best way to access MDF data for all users.
    An internal Query object is used to make queries. From the user's perspective,
    an instantiation of Forge will black-box searching.

    Public Variables:
    local_ep is the endpoint ID of the local Globus Connect Personal endpoint.

    Methods:
    __init__ handles authentication with Globus Auth.
    match_term adds simple terms to the query.
    match_field adds a field:value pair to the query.
    match_sources specifies `source_name`s to search for results in.
    match_elements specifies element abbreviations to match.
    search executes a search.
    search_by_elements executes a search for given elements in given sources.
    aggregate_source returns all records for a given source.
    reset_query destroys the current query and starts a fresh one.
    http_download saves the data files associated with results to disk with HTTPS.
    globus_download saves the data files associated with results to disk with Globus Transfer.
    http_stream yields a generator to fetch data files in sequence.
    http_return returns all the data files at once.
    """
    __index = "mdf"
    __services = ["mdf", "transfer", "search"]
    __app_name = "MDF_Forge"

    def __init__(self, **kwargs):
        """Initialize the Forge instance.

        Keyword Arguments:
        index (str): The Globus Search index to search on.
        services (list of str): The services to authenticate for.
        local_ep (str): The endpoint ID of the local Globus Connect Personal endpoint.
        """
        self.__index = kwargs.get('index', self.__index)
        self.__services = kwargs.get('services', self.__services)
        self.local_ep = kwargs.get("local_ep", None)

        clients = toolbox.login(credentials={
                                "app_name": self.__app_name,
                                "services": self.__services,
                                "index": self.__index})
        self.__search_client = clients["search"]
        self.__transfer_client = clients["transfer"]
        self.__mdf_authorizer = clients["mdf"]

        self.__query = Query(self.__search_client)


    @property
    def search_client(self):
        return self.__search_client


    @property
    def transfer_client(self):
        return self.__transfer_client


    @property
    def mdf_authorizer(self):
        return self.__mdf_authorizer

#################################################
##  Core functions
#################################################

    def match_term(self, term, required=True, new_group=False):
        """Add a term to the query.

        Arguments:
        term (str): The term to add.
        required (bool): If True, will add term with AND. If False, will use OR. Default True.
        new_group (bool): If True, will separate term into new parenthetical group. If False, will not. Default False.

        Returns:
        self (Forge): For chaining.
        """
        # If not the start of the query string, add an AND or OR
        if self.__query.query != "(":
            if required:
                self.__query.and_join(new_group)
            else:
                self.__query.or_join(new_group)
        self.__query.term(term)
        return self


    def match_field(self, field, value, required=True, new_group=False):
        """Add a field:value term to the query.
        Matches will have field == value.

        Arguments:
        field (str): The field to look in for the value.
            The field must be namespaced according to Elasticsearch rules, which means a dot to dive into dictionaries.
            Ex. "mdf.source_name" is the "source_name" field of the "mdf" dictionary.
            If no namespace is provided, the default ("mdf.") will be used.
        value (str): The value to match.
        required (bool): If True, will add term with AND. If False, will use OR. Default True.
        new_group (bool): If True, will separate term into new parenthetical group. If False, will not. Default False.
 
        Returns:
        self (Forge): For chaining.
        """
        # If not the start of the query string, add an AND or OR
        if self.__query.query != "(":
            if required:
                self.__query.and_join(new_group)
            else:
                self.__query.or_join(new_group)
        # Add default namespacing if not present
        if "." not in field:
            field = "mdf." + field
        self.__query.field(field, value)
        return self


    def search(self, q=None, advanced=False, limit=SEARCH_LIMIT, info=False, reset_query=True):
        """Execute a search and return the results.

        Arguments:
        q (str): The query to execute. Defaults to the current query, if any. There must be some query to execute.
        advanced (bool): If True, will submit query in "advanced" mode, which enables searches other than basic fulltext.
                         If False, only basic fulltext term matches will be supported.
                         Default False.
                         This value can change to True automatically if the query is built using advanced features, such as match_field.
        limit (int): The maximum number of results to return. The max for this argument is the SEARCH_LIMIT imposed by Globus Search.
        info (bool): If False, search will return a list of the results.
                     If True, search will return a tuple containing the results list, and other information about the query.
                     Default False.
        reset_query (bool): If True, will destroy the query after execution and start a fresh one. Does nothing if False.
                            Default True.

        Returns:
        list (if info=False): The results.
        tuple (if info=True): The results, and a dictionary of query information.
        """
        res = self.__query.search(q=q, advanced=advanced, limit=limit, info=info)
        if reset_query:
            self.reset_query()
        return res


    def aggregate(self, q=None, scroll_size=SEARCH_LIMIT, reset_query=True):
        """Perform an advanced query, and return all matching results.
        Will automatically preform multiple queries in order to retrieve all results.

        Note that all aggregate queries run in advanced mode.

        Arguments:
        q (str): The query to execute. Defaults to the current query, if any. There must be some query to execute.
        scroll_size (int): Minimum number of records returned per query
        reset_query (bool): If True, will destroy the query after execution and start a fresh one. Does nothing if False.
                            Default True.

        Returns:
        list of dict: All matching records
        """
        res = self.__query.aggregate(q=q, scroll_size=scroll_size)
        if reset_query:
            self.reset_query()
        return res


    def reset_query(self):
        """Destroy the current query and create a fresh, clean one."""
        del self.__query
        self.__query = Query(self.__search_client)


#################################################
##  Helper functions
#################################################

    def match_sources(self, sources):
        """Add sources to match to the query.

        Arguments:
        sources (str or list of str): The sources to match.
 
        Returns:
        self (Forge): For chaining.
        """
        if not sources:
            print("Error: No sources specified.")
            return self
        if not isinstance(sources, list):
            sources = [sources]
        self.match_field(field="mdf.source_name", value=",".join(sources), required=True, new_group=True)
        return self


    def match_elements(self, elements, match_all=True):
        """Add elemental abbreviations to the query.

        Arguments:
        elements (str or list of str): The elements to match.
        match_all (bool): If True, will add with AND. If False, will use OR. Default True.
 
        Returns:
        self (Forge): For chaining.
        """
        if not elements:
            print("Error: No elements specified.")
            return self
        if not isinstance(elements, list):
            elements = [elements]

        if match_all:
            # First source should be in separate group (and required)
            self.match_field(field="mdf.elements", value=elements[0], required=True, new_group=True)
            # Other sources should stay in that group
            for element in elements[1:]:
                self.match_field(field="mdf.elements", value=element, required=match_all, new_group=False)
        else:
            self.match_field(field="mdf.elements", value=",".join(elements), required=True, new_group=True)
        return self


#################################################
##  Premade searches
#################################################

    def search_by_elements(self, elements=[], sources=[], limit=None, match_all=True, info=False):
        """Execute a search for the given elements in the given sources.
        search_by_elements([x], [y]) is equivalent to match_elements([x]).match_sources([y]).search()
        Note that this method does use terms from the current query.

        Arguments:
        elements (list of str): The elements to match. Default [].
        sources (list of str): The sources to match. Default [].
        limit (int): The maximum number of results to return. The max for this argument is the SEARCH_LIMIT imposed by Globus Search.
        match_all (bool): If True, will add elements with AND. If False, will use OR. Default True.
        info (bool): If False, search will return a list of the results.
                     If True, search will return a tuple containing the results list, and other information about the query.
                     Default False.

        Returns:
        list (if info=False): The results.
        tuple (if info=True): The results, and a dictionary of query information.
        """
        return self.match_elements(elements, match_all=match_all).match_sources(sources).search(limit=limit, info=info)


    def aggregate_source(self, sources):
        """Aggregate all records from a given source.
        There is no limit to the number of results returned.
        Please beware of aggregating very large datasets.

        Arguments:
        sources (str or list of str): The source to aggregate.
        
        Returns:
        list of dict: All of the records from the source.
        """
        return self.match_sources(sources).aggregate()


#################################################
##  Data retrieval functions
#################################################

    def http_download(self, results, dest=".", preserve_dir=False, verbose=True):
        """Download data files from the provided results using HTTPS.
        For more than HTTP_NUM_LIMIT (defined above) files, you should use globus_download(), which uses Globus Transfer.

        Arguments:
        results (dict): The records from which files should be fetched.
                        This should be the return value of a search method.
        dest (str): The destination path for the data files on the local machine. Default current directory.
        preserve_dir (bool): If True, the directory structure for the data files will be recreated at the destination.
                             If False, only the data files themselves will be saved.
                            Default False.
        verbose (bool): If True, status and progress messages will be printed.
                        If False, only error messages will be printed.
                        Default True.
        """
        # If results have info attached, remove it
        if type(results) is tuple:
            results = results[0]
        if len(results) > HTTP_NUM_LIMIT:
            print("Error: Too many results supplied. Use globus_download() for fetching more than " + str(HTTP_NUM_LIMIT) + " entries.")
            return {
                "success": False,
                "message": "Too many results supplied. Use globus_download() for fetching more than " + str(HTTP_NUM_LIMIT) + " entries."
                }
        for res in tqdm(results, desc="Fetching files", disable= not verbose):
            for key in tqdm(res["mdf"]["links"].keys(), desc="Fetching files", disable=True):  # not verbose):
                dl = res["mdf"]["links"][key]
                host = dl.get("http_host", None) if type(dl) is dict else None
                if host:
                    remote_path = dl["path"]
                    # local_path should be either dest + whole path or dest + filename, depending on preserve_dir
                    local_path = os.path.normpath(dest + "/" + dl["path"]) if preserve_dir else os.path.normpath(dest + "/" + os.path.basename(dl["path"]))
                    # Make dirs for storing the file if they don't exist
                    # preserve_dir doesn't matter; local_path has accounted for it already
                    try:
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    # If dest is current dir and preserve_dir=False, there are no dirs to make and os.makedirs() will raise FileNotFoundError.
                    # Since it means all dirs required exist, it can be swallowed.
                    except FileNotFoundError:
                        pass
                    # Check if file already exists, change filename if necessary
                    collisions = 0
                    while os.path.exists(local_path):
                        # Find period marking extension, if exists
                        # Will be after last slash
                        index = local_path.rfind(".", local_path.rfind("/"))
                        if index < 0:
                            ext = ""
                        else:
                            ext = local_path[index:]
                            local_path = local_path[:index]
                        # Check if already added number to end
                        old_add = "("+str(collisions)+")"
                        collisions += 1
                        new_add = "("+str(collisions)+")"
                        if local_path.endswith(old_add):
                            local_path = local_path[:-len(old_add)] + new_add + ext
                        else:
                            local_path = local_path + new_add + ext
                    headers = {}
                    self.__mdf_authorizer.set_authorization_header(headers)
                    response = requests.get(host+remote_path, headers=headers)
                    # Handle first 401 by regenerating auth headers
                    if response.status_code == 401:
                        self.__mdf_authorizer.handle_missing_authorization()
                        self.__mdf_authorizer.set_authorization_header(headers)
                        self.response = requests.get(host+remote_path, headers=headers)
                    # Handle other errors by passing the buck to the user
                    if response.status_code != 200:
                        print("Error", response.status_code, " when attempting to access '", host+remote_path, "'", sep="")
                    else:
                        # Write out the binary response content
                        with open(local_path, 'wb') as output:
                            output.write(response.content)


    def globus_download(self, results, dest=".", dest_ep=None, preserve_dir=False, wait_for_completion=True, verbose=True):
        """Download data files from the provided results using Globus Transfer.
        This method requires Globus Connect to be installed on the destination endpoint.

        Arguments:
        results (dict): The records from which files should be fetched.
                        This should be the return value of a search method.
        dest (str): The destination path for the data files on the local machine. Default current directory.
        preserve_dir (bool): If True, the directory structure for the data files will be recreated at the destination.
                                The path to tne new files will be relative to the `dest` path
                             If False, only the data files themselves will be saved.
                             Default False.
        wait_for_completion (bool): If True, will block until the transfer is finished.
                                    If False, will not block.
                                    Default True.
        verbose (bool): If True, status and progress messages will be printed.
                        If False, only error messages will be printed.
                        Default True.

        Returns:
        list of str: task IDs of the Gloubs transfers
        """
        dest = os.path.abspath(dest)
        # If results have info attached, remove it
        if type(results) is tuple:
            results = results[0]
        if not dest_ep:
            if not self.local_ep:
                self.local_ep = toolbox.get_local_ep(self.__transfer_client)
            dest_ep = self.local_ep

        # Assemble the transfer data
        tasks = {}
        filenames = set()
        for res in tqdm(results, desc="Processing records", disable= not verbose):
            for key in tqdm(res["mdf"]["links"].keys(), desc="Fetching files", disable=True): 

                # Get the location of the data
                dl = res["mdf"]["links"][key]
                host = dl.get("globus_endpoint", None) if type(dl) is dict else None

                # If the data is on a Globus Endpoint
                #   LW 9Aug17: Should we at least throw a warning if the data isn't on an endpoint?
                if host:
                    remote_path = dl["path"]
                    # local_path should be either dest + whole path or dest + filename, depending on preserve_dir
                    if preserve_dir:
                        local_path = os.path.abspath(dest + remote_path) # remote_path is absolute, so os.path.join does not work!
                    else:
                        local_path = os.path.abspath(os.path.join(dest, os.path.basename(remote_path)))

                    # Make dirs for storing the file if they don't exist
                    # preserve_dir doesn't matter; local_path has accounted for it already
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    try:
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    # If dest is current dir and preserve_dir=False, there are no dirs to make and os.makedirs() will raise FileNotFoundError.
                    # Since it means all dirs required exist, it can be swallowed.
                    except FileNotFoundError:
                        pass

                    if not preserve_dir:
                        # Check if file already exists, change filename if necessary
                        #   The pattern is to add a number just before the extension (e.g., myfile(1).ext)
                        collisions = 0
                        while os.path.exists(local_path) or local_path in filenames:
                            # Find period marking extension, if exists
                            # Will be after last slash
                            index = local_path.rfind(".", local_path.rfind("/"))
                            if index < 0:
                                ext = ""
                            else:
                                ext = local_path[index:]
                                local_path = local_path[:index]
                            # Check if already added number to end
                            old_add = "("+str(collisions)+")"
                            collisions += 1
                            new_add = "("+str(collisions)+")"
                            if local_path.endswith(old_add):
                                local_path = local_path[:-len(old_add)] + new_add + ext
                            else:
                                local_path = local_path + new_add + ext

                    # Add data to a transfer data object
                    #   LW 11Aug17: TODO, handle transfers with huge number of files
                    #      - If a TransferData object is too large. Globus might timeout before it can be completely uploaded
                    #        So, we need to be able to check the size of the TD object, and - if need be - send it early
                    if host not in tasks.keys():
                        tasks[host] = globus_sdk.TransferData(self.__transfer_client, host, dest_ep, verify_checksum=True)
                    tasks[host].add_item(remote_path, local_path)
                    filenames.add(local_path)

        # Submit the jobs
        submissions = []
        for td in tqdm(tasks.values(), desc="Submitting transfers", disable= not verbose):
            result = self.__transfer_client.submit_transfer(td)
            if result["code"] != "Accepted":
                print("Error submitting transfer:", result["message"])
            else:
                if wait_for_completion:
                    while not self.__transfer_client.task_wait(result["task_id"], timeout=60, polling_interval=10):
                        if verbose:
                            print("Transferring...")
                submissions.append(result["task_id"])
        if verbose:
            print("All transfers submitted")
            print("Task IDs:", "\n".join(submissions))
        return submissions

    def http_stream(self, results, verbose=True):
        """Yield data files from the provided results using HTTPS, through a generator.
        For more than HTTP_NUM_LIMIT (defined above) files, you should use globus_download(), which uses Globus Transfer.

        Arguments:
        results (dict): The records from which files should be fetched.
                        This should be the return value of a search method.
        verbose (bool): If True, status and progress messages will be printed.
                        If False, only error messages will be printed.
                        Default True.

        Yields:
        str: Text of each data file.
        """
        # If results have info attached, remove it
        if type(results) is tuple:
            results = results[0]
        if len(results) > HTTP_NUM_LIMIT:
            print("Too many results supplied. Use globus_download() for fetching more than " + str(HTTP_NUM_LIMIT) + " entries.")
            return {
                "success": False,
                "message": "Too many results supplied. Use globus_download() for fetching more than " + str(HTTP_NUM_LIMIT) + " entries."
                }
        for res in results:
            for key in res["mdf"]["links"].keys():
                dl = res["mdf"]["links"][key]
                host = dl.get("http_host", None) if type(dl) is dict else None
                if host:
                    remote_path = dl["path"]
                    headers = {}
                    self.__mdf_authorizer.set_authorization_header(headers)
                    response = requests.get(host+remote_path, headers=headers)
                    # Handle first 401 by regenerating auth headers
                    if response.status_code == 401:
                        self.__mdf_authorizer.handle_missing_authorization()
                        self.__mdf_authorizer.set_authorization_header(headers)
                        self.response = requests.get(host+remote_path, headers=headers)
                    # Handle other errors by passing the buck to the user
                    if response.status_code != 200:
                        print("Error", response.status_code, " when attempting to access '", host+remote_path, "'", sep="")
                    else:
                        yield response.text


    def http_return(self, results, verbose=True):
        """Return data files from the provided results using HTTPS.
        For more than HTTP_NUM_LIMIT (defined above) files, you should use globus_download(), which uses Globus Transfer.

        Arguments:
        results (dict): The records from which files should be fetched.
                        This should be the return value of a search method.
        verbose (bool): If True, status and progress messages will be printed.
                        If False, only error messages will be printed.
                        Default True.

        Returns:
        list of str: Text data of the data files.
        """
        return list(self.http_stream(results, verbose=verbose))



class Query:
    """The Query class is meant for internal Forge use. Users should not instantiate a Query object directly,
    as Forge manages all the functions a user might need, but advanced users may do so at their own risk. Using Query
    directly is an unsupported behavior and may have unexpected results or unlisted changes in the future.

    Queries may end up wrapped in parentheses, which has no direct effect on the search. Adding terms must be chained
    with .and() or .or(). Terms will not have spaces in between otherwise, and it is desirable to be explicit about
    which terms are required.
    """
    def __init__(self, search_client, q="(", limit=None, advanced=False):
        """Initialize the Query instance.

        Arguments:
        search_client (SearchClient): The Globus Search client to use for searching.
        q (str): The query string to start with. Default nothing.
        limit: The maximum number of results to return. Default None.
        advanced: If True, will submit query in "advanced" mode, which enables searches other than basic fulltext.
                  If False, only basic fulltext term matches will be supported.
                  Default False.
        """
        self.__search_client = search_client
        self.query = q
        self.limit = limit
        self.advanced = advanced


    def __clean_query_string(self, q):
        """Clean up a query string.
        This method does not access self, so that a search will not change state.
        """
        q = q.strip().replace("()", "")
        if q.endswith("("):
            q = q[:-1]
        # Remove misplaced AND/OR at end
        if q[-3:] == "AND":
            q = q[:-3]
        elif q[-2:] == "OR":
            q = q[:-2]

        # Balance parentheses
        while q.count("(") > q.count(")"):
            q += ")"
        while q.count(")") > q.count("("):
            q = "(" + q
        
        return q


    def term(self, term):
        """Add a term to the query.

        Arguments:
        term (str): The term to add.

        Returns:
        self (Query): For chaining.
        """
        self.query += term
        return self


    def field(self, field, value):
        """Add a field:value term to the query.
        Matches will have field == value.
        This method sets advanced=True.

        Arguments:
        field (str): The field to look in for the value.
        value (str): The value to match.
 
        Returns:
        self (Query): For chaining.
        """
        self.query += field + ":" + value
        # Field matches are advanced queries
        self.advanced = True
        return self


    def and_join(self, close_group=False):
        """Combine terms with AND.
        There must be a term added before using this method.

        Arguments:
        close_group (bool): If True, will end the current group and start a new one.
                      If False, will continue current group.
                      Example: If the current query is "(term1"
                          .and(close_group=True) => "(term1) AND ("
                          .and(close_group=False) => "(term1 AND "

        Returns:
        self (Query): For chaining.
        """
        last = self.query.strip(" ()")[-3:]
        # Check that the query has terms
        if not last:
            print("Error: You must add a term before using .and(). The current query has not been changed.")
        # Check to make sure there is a term before the AND
        elif last == "AND" or last[1:] == "OR":
            print("Error: You must add a term between each AND or OR. The current query has not been changed.")
        else:
            self.query += ") AND (" if close_group else " AND "
        return self


    def or_join(self, close_group=False):
        """Combine terms with OR.
        There must be a term added before using this method.

        Arguments:
        close_group (bool): If True, will end the current group and start a new one.
                      If False, will continue current group.
                      Example: If the current query is "(term1"
                          .or(close_group=True) => "(term1) OR("
                          .or(close_group=False) => "(term1 OR "

        Returns:
        self (Query): For chaining.
        """
        last = self.query.strip(" ()")[-3:]
        # Check that the query has terms
        if not last:
            print("Error: You must add a term before using .or(). The current query has not been changed.")
        # Check to make sure there is a term before the OR
        elif last == "AND" or last[1:] == "OR":
            print("Error: You must add a term between each AND or OR. The current query has not been changed.")
        else:
            self.query += ") OR (" if close_group else " OR "
        return self


    def search(self, q=None, advanced=None, limit=10, info=False):
        """Execute a search and return the results.

        Arguments:
        q (str): The query to execute. Defaults to the current query, if any. There must be some query to execute.
        advanced (bool): If True, will submit query in "advanced" mode, which enables searches other than basic fulltext.
                         If False, only basic fulltext term matches will be supported.
                         Default False.
                         This value can change to True automatically if the query is built using advanced features, such as match_field.
        limit (int): The maximum number of results to return. The max for this argument is the SEARCH_LIMIT imposed by Globus Search. Default 10.
        info (bool): If False, search will return a list of the results.
                     If True, search will return a tuple containing the results list, and other information about the query.
                     Default False.

        Returns:
        list (if info=False): The results.
        tuple (if info=True): The results, and a dictionary of query information.
        """
        if q is None:
            q = self.query
        if not q.strip("()"):
            print("Error: No query specified")
            return ([], {"error": "No query specified"}) if info else []
        if advanced is None or self.advanced:
            advanced = self.advanced
        if limit is None:
            limit = self.limit or SEARCH_LIMIT
        if limit > SEARCH_LIMIT:
            limit = SEARCH_LIMIT

        q = self.__clean_query_string(q)

        # Simple query (max 10k results)
        qu = {
            "q": q,
            "advanced": advanced,
            "limit": limit
            }
        res = toolbox.gmeta_pop(self.__search_client.structured_search(qu), info=info)
        # Add additional info
        if info:
            res[1]["query"] = qu
        return res


    def aggregate(self, q=None, scroll_size=SEARCH_LIMIT):
        """Gather all record results that match a specific query

        Note that all aggregate queries run in advanced mode.

        Arguments:
        q (str): The query to execute. Defaults to the current query, if any. There must be some query to execute.
        advanced (bool): If True, will submit query in "advanced" mode, which enables searches other than basic fulltext.
                         If False, only basic fulltext term matches will be supported.
                         Default False.
                         This value can change to True automatically if the query is built using advanced features, such as match_field.
        scroll_size (int): Maximum number of records requested per request

        Returns:
        list of dict: All matching records
        """
        if q is None:
            q = self.query
        if not q.strip("()"):
            print("Error: No query specified")
            return ([], {"error": "No query specified"}) if info else []

        q = self.__clean_query_string(q)

        q += " AND mdf.resource_type:record"

        # Get the total number of records
        result = self.__search_client.search(q, limit=0, advanced=True)
        total = result['total']

        # Scroll until all results are found
        output = []
        scroll_pos = 1
        with tqdm(total=total) as pbar:
            while len(output) < total:

                # Scroll until the width is small enough to get all records
                #   `scroll_id`s are unique to each dataset. If multiple datasets
                #   match a certain query, the total number of matching records
                #   may exceed the maximum that search will return - even if the
                #   scroll width is much smaller than that maximum
                scroll_width = scroll_size
                while True:
                    result_records = self.__search_client.search("(" + q +
                                                                 ') AND mdf.scroll_id:>=%d AND mdf.scroll_id:<%d' % (
                                                                     scroll_pos, scroll_pos + scroll_width),
                                                                 advanced=True, limit=SEARCH_LIMIT)

                    # Check to make sure that all the matching records were returned
                    if result_records['total'] <= result_records['count']:
                        break

                    # If not, reduce the scroll width
                    scroll_width = int(scroll_width * (result_records['count'] / result_records['total']))

                # Append the results to the output
                output.extend(toolbox.gmeta_pop(result_records))
                pbar.update(result_records['count'])
                scroll_pos += scroll_width

        return output

